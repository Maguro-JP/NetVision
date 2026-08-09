"""構成図を Excel のセルに描く。

現場の構成図は Excel で回っていることが多い。SVG は貼るだけなら良いが、
手元で直せない。セルに枠線で描いてあれば、行を増やす・機器を1台足す・
コメントを入れるといった直しがその場でできる。

やり方は方眼紙と同じで、細かいセルを敷き、

* 機器 = セルを結合して罫線で囲んだ箱
* 結線 = セルの辺に罫線を引いた折れ線

にする。上位を左、下位を右に並べ、機器の左に上りポート、右に下りポートを書く。

全体を1枚に描こうとしない。 拠点が数千あるところで全体図は使えないので、
拠点で絞って出すことを前提にしている。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.device import DeviceType
from app.services import localtime, topology_ports, topology_table
from app.services.topology import (
    DEFAULT_LAYER,
    TIER_ORDER,
    get_graph,
    hierarchy_rank,
    scope_graph,
)

# 方眼の細かさ。箱の大きさと、線を通す余白をこれで決める
BOX_W = 8  # 箱の幅（列）
BOX_H = 3  # 箱の高さ（行）
GAP_W = 8  # 段と段の間（列）。ここに縦線と上りポート名が入る
GAP_H = 1  # 箱と箱の間（行）

_HEADER_ROWS = 3  # 見出しの分


@dataclass
class _Placed:
    device_id: int
    hostname: str
    model: str
    site_name: str
    depth: int
    row: int  # 箱の上端（1 始まり）
    uplinks: list[str] = field(default_factory=list)
    downlinks: list[str] = field(default_factory=list)
    parent: int | None = None

    @property
    def col(self) -> int:
        return 1 + self.depth * (BOX_W + GAP_W)

    @property
    def middle(self) -> int:
        return self.row + BOX_H // 2


def _layout(
    graph,
    ports: dict[int, topology_ports.DevicePorts],
    rank: dict[int, int] | None = None,
) -> list[_Placed]:
    """上位を左にした木に並べる。

    根は網全体で見て一番上位の機器（`hierarchy_rank` が小さいもの）。
    そこから幅優先でたどって親子を決める。拠点だけを出したときも、
    その拠点の上位（対向）が左端に来る。
    """
    nodes = {n.device_id: n for n in graph.nodes}
    tier = {
        n.device_id: TIER_ORDER.get(DeviceType(n.device_type), 7)
        for n in graph.nodes
        if n.device_type in {t.value for t in DeviceType}
    }
    for node in graph.nodes:
        tier.setdefault(node.device_id, 7)

    # 隣接関係を作り、上位から幅優先でたどって木にする。
    # 段（種別）だけで親を決めると、拠点ルータ→支社ルータのような
    # 同じ種別が縦に並ぶ構成が全部ばらばらの根になってしまう
    neighbours: dict[int, list[int]] = {}
    for edge in graph.edges:
        if edge.source in nodes and edge.target in nodes:
            neighbours.setdefault(edge.source, []).append(edge.target)
            neighbours.setdefault(edge.target, []).append(edge.source)

    order = sorted(
        (n.device_id for n in graph.nodes),
        key=lambda device_id: (
            # 網全体で見た「上位からの遠さ」。本数で決めると、配下の多い
            # 支社ルータがセンターより上に来てしまう
            (rank or {}).get(device_id, 0),
            tier[device_id],
            nodes[device_id].hostname,
        ),
    )

    parent: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    seen: set[int] = set()
    roots: list[int] = []
    for start in order:
        if start in seen:
            continue
        roots.append(start)
        seen.add(start)
        queue = [start]
        while queue:
            current = queue.pop(0)
            for neighbour in sorted(neighbours.get(current, []), key=lambda i: nodes[i].hostname):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                parent[neighbour] = current
                children.setdefault(current, []).append(neighbour)
                queue.append(neighbour)

    placed: list[_Placed] = []
    row = _HEADER_ROWS + 1

    def walk(device_id: int, depth: int, at_row: int) -> int:
        """自分を `at_row` に置き、子を右へ並べる。使った一番下の行を返す。

        最初の子は親と同じ行に置く。1台ずつ下げると、上位から下位へ
        一直線に繋がっているだけの構成が階段状になって、紙が横にも縦にも伸びる。
        """
        nonlocal row
        node = nodes[device_id]
        entry = topology_ports.DevicePorts(
            device_id=device_id,
            hostname=node.hostname,
            site_name=node.site_name or "",
            model=node.model,
            tier=tier[device_id],
            total_ports=None,
        )
        detail = ports.get(device_id, entry)
        placed.append(
            _Placed(
                device_id=device_id,
                hostname=node.hostname,
                model=node.model,
                site_name=node.site_name or "",
                depth=depth,
                row=at_row,
                uplinks=[p.interface for p in detail.uplinks],
                downlinks=[p.interface for p in detail.downlinks],
                parent=parent.get(device_id),
            )
        )
        bottom = at_row
        for index, child in enumerate(
            sorted(children.get(device_id, []), key=lambda i: nodes[i].hostname)
        ):
            child_row = at_row if index == 0 else bottom + BOX_H + GAP_H
            bottom = max(bottom, walk(child, depth + 1, child_row))
        return bottom

    for root in roots:
        row = walk(root, 0, row) + BOX_H + GAP_H
    return placed


def to_xlsx(
    db: Session,
    layer: str = DEFAULT_LAYER,
    *,
    site_id: int | None = None,
    scope: str = "peers",
) -> bytes:
    """構成図・結線表・ポート一覧・ポート割付を1つのファイルにまとめる。

    ボタンを分けるより、1つ開けば全部入っている方が配りやすい。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    whole = get_graph(db, layer)
    # 上下関係は網全体で見る。拠点だけを切り出した中で決めると、
    # その拠点でたまたま枝の多い機器が一番上に来てしまう
    rank = hierarchy_rank(whole)

    graph = scope_graph(whole, site_id=site_id, scope=scope)
    ports = {p.device_id: p for p in topology_ports.collect(db)}
    placed = _layout(graph, ports, rank)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "構成図"

    thin = Side(style="thin", color="808080")
    line = Side(style="medium", color="333333")
    box_fill = PatternFill("solid", start_color="EEF3FA", end_color="EEF3FA")
    outside_fill = PatternFill("solid", start_color="F2F2F2", end_color="F2F2F2")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    generated = localtime.format(localtime.now(), with_zone=True)
    title = sheet.cell(row=1, column=1, value="構成図")
    title.font = Font(bold=True, size=14)
    sheet.cell(
        row=2,
        column=1,
        value=f"{generated} 時点 / {len(graph.nodes)}台 {len(graph.edges)}本"
        + (" / 上位を左、下位を右に並べています" if placed else ""),
    ).font = Font(size=9, color="666666")

    outside = {n.device_id for n in graph.nodes if n.outside}
    by_id = {p.device_id: p for p in placed}
    # どのポート同士で繋がっているか。機器の並び順ではなく、線ごとに引く
    port_of: dict[tuple[int, int], tuple[str, str]] = {}
    for edge in graph.edges:
        port_of.setdefault(
            (edge.source, edge.target), (edge.source_interface, edge.target_interface)
        )
        port_of.setdefault(
            (edge.target, edge.source), (edge.target_interface, edge.source_interface)
        )

    def draw_box(entry: _Placed) -> None:
        col, row = entry.col, entry.row
        sheet.merge_cells(
            start_row=row, start_column=col, end_row=row + BOX_H - 1, end_column=col + BOX_W - 1
        )
        cell = sheet.cell(row=row, column=col)
        cell.value = f"{entry.hostname}\n{entry.model}\n{entry.site_name}"
        cell.alignment = center
        cell.fill = outside_fill if entry.device_id in outside else box_fill
        cell.font = Font(size=9, italic=entry.device_id in outside)
        for r in range(row, row + BOX_H):
            for c in range(col, col + BOX_W):
                sheet.cell(row=r, column=c).border = Border(
                    left=line if c == col else thin,
                    right=line if c == col + BOX_W - 1 else thin,
                    top=line if r == row else thin,
                    bottom=line if r == row + BOX_H - 1 else thin,
                )

    def draw_link(child: _Placed, parent_entry: _Placed) -> None:
        """親の右端から子の左端へ、縦横だけの折れ線を引く。"""
        bus = parent_entry.col + BOX_W + GAP_W // 2
        # 親側の横線
        for c in range(parent_entry.col + BOX_W, bus + 1):
            _line_top(sheet, parent_entry.middle, c, line)
        # 縦線（親の高さから子の高さまで）
        low, high = sorted((parent_entry.middle, child.middle))
        for r in range(low, high + 1):
            _line_left(sheet, r, bus, line)
        # 子側の横線
        for c in range(bus, child.col):
            _line_top(sheet, child.middle, c, line)

        # ポート名はその機器の隣に書く。親の右に下りポート、子の左に上りポート。
        # 線の真ん中に2つ並べると「どちらがどちら側か」を覚える必要が出る
        pair = port_of.get((parent_entry.device_id, child.device_id))
        if pair:
            down, up = pair
            if down:
                cell = sheet.cell(
                    row=parent_entry.middle - 1, column=parent_entry.col + BOX_W, value=down
                )
                cell.font = Font(size=8, color="444444")
                cell.alignment = Alignment(horizontal="left")
            if up:
                cell = sheet.cell(row=child.middle - 1, column=max(child.col - 5, bus), value=up)
                cell.font = Font(size=8, color="444444")
                cell.alignment = Alignment(horizontal="right")

    for entry in placed:
        draw_box(entry)
    for entry in placed:
        if entry.parent is not None and entry.parent in by_id:
            draw_link(entry, by_id[entry.parent])

    # 方眼にする。列を細く、行を低くしないと図に見えない
    depth_max = max((e.depth for e in placed), default=0)
    for column in range(1, (depth_max + 1) * (BOX_W + GAP_W) + GAP_W + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 3.2
    for row in range(_HEADER_ROWS + 1, (placed[-1].row + BOX_H + 2) if placed else 40):
        sheet.row_dimensions[row].height = 14
    sheet.sheet_view.showGridLines = False

    # --- 表のシート。同じファイルに入れておけば、配るのは1つで済む ---
    _append_table(
        workbook.create_sheet("結線表"),
        topology_table.HEADERS,
        topology_table.rows(db, layer, site_id=site_id),
        Font,
        get_column_letter,
    )
    _append_table(
        workbook.create_sheet("ポート一覧"),
        topology_ports.HEADERS,
        topology_ports.rows(db, site_id=site_id),
        Font,
        get_column_letter,
    )

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _line_top(sheet, row: int, column: int, line) -> None:
    from openpyxl.styles import Border

    current = sheet.cell(row=row, column=column).border
    sheet.cell(row=row, column=column).border = Border(
        left=current.left, right=current.right, top=line, bottom=current.bottom
    )


def _line_left(sheet, row: int, column: int, line) -> None:
    from openpyxl.styles import Border

    current = sheet.cell(row=row, column=column).border
    sheet.cell(row=row, column=column).border = Border(
        left=line, right=current.right, top=current.top, bottom=current.bottom
    )


def _append_table(sheet, headers, rows, Font, get_column_letter) -> None:
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for index, header in enumerate(headers, start=1):
        width = max(len(header) * 2 + 2, 12)
        for row in rows:
            width = max(width, min(len(str(row[index - 1])) + 2, 40))
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
