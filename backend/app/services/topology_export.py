"""トポロジー図をファイルに書き出す。

設計書・報告書・稟議資料に貼るための出力。スクリーンショットで済ませると

- 印刷や拡大で潰れる（ラスタなので）
- 画面に入りきらない範囲が切れる
- 貼り直しのたびに撮り直しになる

ので、ベクタ（SVG）で図そのものを出す。SVG は Word / PowerPoint / Excel が
そのまま挿入でき（Office 2016 以降）、ブラウザでも開けて印刷にも耐える。

生成はサーバ側で行う。画面側の描画ライブラリに依存しないので、閉域配布の
バンドルに余計なものが増えず、出力内容をテストできる。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, cos, degrees, sin
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.services import localtime, portlabel, svglayout
from app.services.lifecycle import Severity
from app.services.portlabel import MEDIA_COLOR, MEDIA_LABEL, Media
from app.services.topology import (
    PLANE_NAMES,
    TopologyGraph,
    _box_height,
    _box_width,
    _busiest_sides,
    load_diagram,
)

# 画面の配色に合わせる。印刷しても潰れないよう、白地でも読める濃さにしてある
SEVERITY_COLOR: dict[str, str] = {
    str(Severity.EXPIRED): "#c0392b",
    str(Severity.CRITICAL): "#e07b39",
    str(Severity.WARNING): "#c9a227",
    str(Severity.OK): "#2d8a4e",
    str(Severity.UNKNOWN): "#7a7a7a",
}
SEVERITY_LABEL: dict[str, str] = {
    str(Severity.EXPIRED): "期限切れ",
    str(Severity.CRITICAL): "90日以内",
    str(Severity.WARNING): "1年以内",
    str(Severity.OK): "余裕あり",
    str(Severity.UNKNOWN): "情報未登録",
}

NODE_W = 190.0
# ポートの種類（GigabitEthernet など）を箱の中に1行入れる分の高さを含む。
# 線のラベルを番号だけにしても、機器を見れば何のポートか分かるようにするため
NODE_H = 78.0
MARGIN = 48.0
LEGEND_H = 62.0

# 日本語が入るので、環境にありそうなものを順に並べる
FONT = "'Yu Gothic', 'Hiragino Kaku Gothic ProN', Meiryo, 'Noto Sans JP', sans-serif"


def _esc(value: object) -> str:
    return escape(str(value if value is not None else ""))


def _clip(value: str | None, limit: int) -> str:
    text = value or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_svg(
    graph: TopologyGraph,
    *,
    title: str = "ネットワーク構成図",
    vlan: int | None = None,
    vrf: str | None = None,
    site_id: int | None = None,
    style: str = "detail",
    port_style: str = "short",
    along_line: bool = False,
    show_peers: bool = True,
) -> str:
    """トポロジを SVG にする。位置は渡されたグラフのまま使う。

    拠点の図は、画面と同じ配置（自拠点と対向）をそのまま描く。
    ここで自拠点だけ残して対向を置き直すと、箱が千切れ、斜め線が出る。

    `vlan` / `vrf` でさらに切ったときだけ、外に出た相手を簡易表示で残す。
    未整理の全台グラフに `site_id` だけ渡された場合も、同じ簡易表示に落とす。

    `style="simple"` は機器名と結線だけの図。型番・IP・保守状態・VLAN を
    一切載せない。作業手順書や現場での指差し確認では、余計な情報が入っている方が
    読み違いのもとになるため、用途で図を分けられるようにしてある。
    """
    simple = style == "simple"
    everything = graph
    nodes = graph.nodes
    # 画面が組んだ拠点図（対向に outside が付いている）は、切らずに描く
    already_site_diagram = any(n.outside for n in graph.nodes)
    if vlan is not None:
        nodes = [n for n in nodes if vlan in n.vlans]
    if vrf is not None:
        nodes = [n for n in nodes if vrf in n.vrfs]
    if already_site_diagram and not show_peers:
        nodes = [n for n in nodes if not n.outside]
    elif site_id is not None and not already_site_diagram:
        nodes = [n for n in nodes if n.site_id == site_id]

    peers: list[_Peer] = []
    if nodes is not graph.nodes:
        keep = {n.device_id for n in nodes}
        # 絞り込みの外に出た相手を、簡易表示で残す。
        # 線ごと消すと、上位への接続が「無い」ように見えてしまう
        peers = _boundary_peers(everything, keep) if show_peers else []
        groups = []
        for group in everything.groups:
            ids = [device_id for device_id in group.device_ids if device_id in keep]
            if ids:
                groups.append(replace(group, device_ids=ids))
        graph = TopologyGraph(
            layer=graph.layer,
            nodes=nodes,
            edges=[e for e in graph.edges if e.source in keep and e.target in keep],
            groups=groups,
        )
    if not nodes:
        return _empty_svg(title, layer=graph.layer)

    busiest_tb, busiest_lr = _busiest_sides(nodes, graph.edges)

    def node_size(device_id: int) -> tuple[float, float]:
        return (
            _box_width(busiest_tb.get(device_id, 0)),
            _box_height(busiest_lr.get(device_id, 0)),
        )

    def display_box(node) -> tuple[float, float, float, float]:
        """保存左上を、広げた箱の中心が保たれる画面座標の箱へ。"""
        w, h = node_size(node.device_id)
        shift_x = (w - NODE_W) / 2
        shift_y = (h - NODE_H) / 2
        return (node.x - shift_x, node.y - shift_y, w, h)

    sized = {n.device_id: display_box(n) for n in nodes}

    min_x = min([sized[n.device_id][0] for n in nodes] + [p.x for p in peers])
    min_y = min([sized[n.device_id][1] for n in nodes] + [p.y for p in peers])
    max_x = max(
        [sized[n.device_id][0] + sized[n.device_id][2] for n in nodes]
        + [p.x + PEER_W for p in peers]
    )
    max_y = max(
        [sized[n.device_id][1] + sized[n.device_id][3] for n in nodes]
        + [p.y + PEER_H for p in peers]
    )
    if graph.groups:
        min_x -= 24
        min_y -= 28
        max_x += 24
        max_y += 24

    # 図の左上を原点側へ寄せ、余白と凡例・見出しの分を確保する
    legend_h = 0.0 if simple else LEGEND_H
    dx = MARGIN - min_x
    dy = MARGIN + 34.0 - min_y
    width = max_x - min_x + MARGIN * 2
    height = max_y - min_y + MARGIN * 2 + 34.0 + legend_h

    at = {
        n.device_id: (sized[n.device_id][0] + dx, sized[n.device_id][1] + dy) for n in nodes
    }
    size_at = {
        n.device_id: (sized[n.device_id][2], sized[n.device_id][3]) for n in nodes
    }
    parts: list[str] = []
    # 凡例に出すのは実際に使われている種別だけ。使っていない色を並べても読み手が困る
    used_media: set[Media] = set()

    # 機器の箱と簡易ノードを先に「埋まっている場所」として登録しておく。
    # ラベルはこれを避けて置く。少し広げるのは、枠に文字が接すると読みにくいため
    placement = svglayout.Placement(
        [
            svglayout.Rect(px, py, px + size_at[device_id][0], py + size_at[device_id][1]).grow(3)
            for device_id, (px, py) in at.items()
        ]
        + [
            svglayout.Rect(p.x + dx, p.y + dy, p.x + dx + PEER_W, p.y + dy + PEER_H).grow(3)
            for p in peers
        ]
    )

    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    # 資料に載る時刻は、サーバの時計ではなく使う人の時刻でなければ意味が無い
    generated = localtime.format(localtime.now(), with_zone=True)
    parts.append(
        f'<text x="{MARGIN:.0f}" y="30" font-size="18" font-weight="700" fill="#1a1a1a">'
        f"{_esc(title)}</text>"
    )
    scope = f"{f' / VLAN {vlan}' if vlan is not None else ''}"
    scope += f"{f' / VRF {vrf}' if vrf else ''}"
    if site_id is not None:
        site_name = next((n.site_name for n in nodes if not n.outside and n.site_name), None)
        if not site_name:
            site_name = next((n.site_name for n in nodes if n.site_name), None)
        scope += f" / {site_name}" if site_name else ""
    # 対象外へ出ていく線も本数に入れる。描いてある線が数に入っていないと、
    # 図と見出しが食い違って見える
    counts = f"{len(nodes)}台 {len(graph.edges)}本"
    if peers:
        counts += f"（外部へ {len(peers)}本）"
    if simple:
        # シンプル図でも「いつ時点の何台か」だけは残す。日付の無い構成図は
        # あとから見たときに信用できないため
        parts.append(
            f'<text x="{width - MARGIN:.0f}" y="30" font-size="11" fill="#6b6b6b" '
            f'text-anchor="end">{_esc(generated)} 時点{_esc(scope)} / '
            f"{_esc(counts)}</text>"
        )
    else:
        parts.append(
            f'<text x="{width - MARGIN:.0f}" y="30" font-size="11" fill="#6b6b6b" '
            f'text-anchor="end">{_esc(generated)} 時点 / レイヤ: {_esc(graph.layer)}'
            f"{_esc(scope)} / {_esc(counts)}</text>"
        )

    for group in graph.groups:
        members = [(at[i], size_at[i]) for i in group.device_ids if i in at]
        if not members:
            continue
        gx = min(pos[0] for pos, _ in members) - 18
        gy = min(pos[1] for pos, _ in members) - 26
        gw = max(pos[0] + size[0] for pos, size in members) - gx + 18
        gh = max(pos[1] + size[1] for pos, size in members) - gy + 18
        label = group.name
        if group.plane and group.plane != "custom":
            label = f"{group.name} · {PLANE_NAMES.get(group.plane, group.plane)}"
        parts.append(
            f'<rect x="{gx:.0f}" y="{gy:.0f}" width="{gw:.0f}" height="{gh:.0f}" rx="10" '
            f'fill="{_esc(group.color)}14" stroke="{_esc(group.color)}" stroke-width="1.6" '
            f'stroke-dasharray="6 4"/>'
            f'<text x="{gx + 10:.0f}" y="{gy + 16:.0f}" font-size="11" fill="{_esc(group.color)}">'
            f"{_esc(label)}</text>"
        )

    # 同じ機器同士を何本も結んでいる場合（LAG など）に、線を平行にずらすための番号。
    # 中心同士を直線で結ぶだけだと完全に重なって1本に見える
    parallel = _parallel_index(graph)

    boxes = {
        n.device_id: svglayout.Rect(
            at[n.device_id][0],
            at[n.device_id][1],
            at[n.device_id][0] + size_at[n.device_id][0],
            at[n.device_id][1] + size_at[n.device_id][1],
        )
        for n in nodes
    }
    peer_boxes = [
        svglayout.Rect(p.x + dx, p.y + dy, p.x + dx + PEER_W, p.y + dy + PEER_H) for p in peers
    ]

    sides: dict[int, tuple[str, str]] = {}
    groups: dict[tuple[int, str], list[tuple[float, int]]] = {}
    boxes_by_id = {
        n.device_id: (
            at[n.device_id][0],
            at[n.device_id][1],
            size_at[n.device_id][0],
            size_at[n.device_id][1],
        )
        for n in nodes
    }
    for edge in graph.edges:
        source, target = at.get(edge.source), at.get(edge.target)
        if source is None or target is None:
            continue
        src_box = boxes_by_id[edge.source]
        dst_box = boxes_by_id[edge.target]
        src_side = edge.source_side or _side_toward(src_box, dst_box)
        dst_side = edge.target_side or _side_toward(dst_box, src_box)
        sides[edge.id] = (src_side, dst_side)
        src_along = dst_box[1] if src_side in ("left", "right") else dst_box[0]
        dst_along = src_box[1] if dst_side in ("left", "right") else src_box[0]
        groups.setdefault((edge.source, src_side), []).append((src_along, edge.id))
        groups.setdefault((edge.target, dst_side), []).append((dst_along, edge.id))
    lane_rank: dict[int, tuple[int, int]] = {}
    face_t: dict[tuple[int, str, int], float] = {}
    edge_by_id = {edge.id: edge for edge in graph.edges}
    for (device_id, side), members in groups.items():
        members.sort()
        box = boxes_by_id[device_id]
        ideals: list[float] = []
        for _, edge_id in members:
            edge = edge_by_id[edge_id]
            other_id = edge.target if edge.source == device_id else edge.source
            ideals.append(_face_t(box, side, boxes_by_id[other_id]))
        for index, (_, edge_id) in enumerate(members):
            lane_rank[edge_id] = (index, len(members))
        spaced = _space_face_ts(
            ideals,
            box[3] if side in ("left", "right") else box[2],
        )
        for (_, edge_id), t in zip(members, spaced, strict=False):
            face_t[(device_id, side, edge_id)] = t

    # --- 結線（ノードより先に描いて背面に回す） ---
    pending: list[tuple[list[tuple[float, float]], object]] = []
    for edge in graph.edges:
        source, target = at.get(edge.source), at.get(edge.target)
        if source is None or target is None:
            continue
        src_box = boxes_by_id[edge.source]
        dst_box = boxes_by_id[edge.target]
        src_side, dst_side = sides.get(edge.id, ("right", "left"))
        rank_i, rank_n = lane_rank.get(edge.id, (0, 1))
        obstacles = [
            rect.grow(2.0)
            for device_id, rect in boxes.items()
            if device_id not in (edge.source, edge.target)
        ] + [rect.grow(2.0) for rect in peer_boxes]
        points = _wire_path(
            src_box,
            dst_box,
            src_side,
            dst_side,
            rank_i,
            rank_n,
            obstacles,
            src_t=face_t.get((edge.source, src_side, edge.id)),
            dst_t=face_t.get((edge.target, dst_side, edge.id)),
        )
        x1, y1 = points[0]
        x2, y2 = points[-1]
        shift = parallel.get(edge.id, 0.0)
        # 折れ線全体を端点どうしの向きでずらすと、縦横の辺が斜めになる。
        # 束の分離は lane rank のバスで足りる。直線のときだけ平行にずらす。
        if shift and len(points) == 2:
            length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
            offset_x = -(y2 - y1) / length * shift
            offset_y = (x2 - x1) / length * shift
            shifted = [(x + offset_x, y + offset_y) for x, y in points]
            if _residual(shifted, obstacles) <= _residual(points, obstacles):
                points = shifted
        pending.append((points, edge))

    gapped = _cut_gaps([item[0] for item in pending])
    stroke = "#4a5364" if simple else "#8a94a6"
    for index, (points, edge) in enumerate(pending):
        dash = "" if (simple or edge.confirmed_both_ways) else ' stroke-dasharray="6 4"'
        for piece in gapped[index]:
            parts.append(
                f'<polyline points="{_path_points(piece)}" fill="none" '
                f'stroke="{stroke}" stroke-width="1.6"{dash}/>'
            )
        x1, y1 = points[0]
        x2, y2 = points[-1]
        if edge.label:
            parts.append(_center_label(*_path_middle(points), edge.label, placement))
        else:
            source = at[edge.source]
            target = at[edge.target]
            sw, sh = size_at[edge.source]
            tw, th = size_at[edge.target]
            source = at[edge.source]
            target = at[edge.target]
            sw, sh = size_at[edge.source]
            tw, th = size_at[edge.target]
            sx, sy = source[0] + sw / 2, source[1] + sh / 2
            tx, ty = target[0] + tw / 2, target[1] + th / 2
            parts.append(
                _port_label(
                    sx,
                    sy,
                    x1,
                    y1,
                    edge.source_interface,
                    port_style,
                    along_line,
                    placement,
                    half_w=sw / 2,
                    half_h=sh / 2,
                )
            )
            parts.append(
                _port_label(
                    tx,
                    ty,
                    x2,
                    y2,
                    edge.target_interface,
                    port_style,
                    along_line,
                    placement,
                    half_w=tw / 2,
                    half_h=th / 2,
                )
            )
            for name in (edge.source_interface, edge.target_interface):
                if name:
                    used_media.add(portlabel.media_of(name))

    # --- 絞り込みの外にいる対向（簡易表示） ---
    for peer in peers:
        anchor = at.get(peer.anchor)
        if anchor is None:
            continue
        px, py = peer.x + dx, peer.y + dy
        aw, ah = size_at.get(peer.anchor, (NODE_W, NODE_H))
        ax, ay = anchor[0] + aw / 2, anchor[1] + ah / 2

        obstacles = [
            rect.grow(2.0) for device_id, rect in boxes.items() if device_id != peer.anchor
        ] + [
            svglayout.Rect(q.x + dx, q.y + dy, q.x + dx + PEER_W, q.y + dy + PEER_H).grow(2.0)
            for q in peers
            if q is not peer
        ]
        src_box = (anchor[0], anchor[1], aw, ah)
        dst_box = (px, py, PEER_W, PEER_H)
        src_side = _side_toward(src_box, dst_box)
        dst_side = _side_toward(dst_box, src_box)
        points = _wire_path(src_box, dst_box, src_side, dst_side, 0, 1, obstacles)
        parts.append(
            f'<polyline points="{_path_points(points)}" fill="none" '
            f'stroke="#8a94a6" stroke-width="1.6"'
            + ("" if peer.confirmed else ' stroke-dasharray="6 4"')
            + "/>"
        )
        parts.append(_peer_box(px, py, peer, simple))
        # 図の中に残っている側のポート名は、通常どおり機器の隣に出す
        label_to = points[1] if len(points) > 1 else points[0]
        parts.append(
            _port_label(
                ax,
                ay,
                *label_to,
                peer.local_interface,
                port_style,
                along_line,
                placement,
                half_w=aw / 2,
                half_h=ah / 2,
            )
        )
        if peer.local_interface:
            used_media.add(portlabel.media_of(peer.local_interface))

    # --- 機器 ---
    for node in nodes:
        x, y = at[node.device_id]
        nw, nh = size_at[node.device_id]
        if simple:
            # 名前だけ。枠は輪郭が分かる程度の細さに留める
            subtitle = ""
            if node.outside and node.site_name:
                subtitle = (
                    f'<text x="{x + nw / 2:.0f}" y="{y + nh / 2 + 18:.0f}" font-size="10" '
                    f'fill="#6b6b6b" text-anchor="middle">{_esc(_clip(node.site_name, 22))}</text>'
                )
            name_y = y + nh / 2 + (0 if subtitle else 5)
            parts.append(
                f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{nw:.0f}" '
                f'height="{nh:.0f}" rx="7" fill="#ffffff" stroke="#4a5364" '
                f'stroke-width="1.6"/>'
                f'<text x="{x + nw / 2:.0f}" y="{name_y:.0f}" font-size="14" '
                f'font-weight="700" fill="#1a1a1a" text-anchor="middle">'
                f"{_esc(_clip(node.hostname, 22))}</text>{subtitle}</g>"
            )
            continue

        color = SEVERITY_COLOR.get(str(node.severity), SEVERITY_COLOR[str(Severity.UNKNOWN)])
        parts.append(
            f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{nw:.0f}" height="{nh:.0f}" '
            f'rx="7" fill="#f7f9fc" stroke="{color}" stroke-width="2"/>'
            # 状態が一目で分かるよう左端に色帯を置く（白黒印刷でも位置で区別できる）
            f'<rect x="{x:.0f}" y="{y:.0f}" width="5" height="{nh:.0f}" '
            f'rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{x + 14:.0f}" y="{y + 22:.0f}" font-size="13" font-weight="700" '
            f'fill="#1a1a1a">{_esc(_clip(node.hostname, 22))}</text>'
        )
        parts.append(
            f'<text x="{x + 14:.0f}" y="{y + 38:.0f}" font-size="10" fill="#5a6270">'
            f"{_esc(_clip(f'{node.vendor} {node.model}'.strip(), 26))}</text>"
        )
        detail = node.mgmt_ip or node.site_name or ""
        if detail:
            parts.append(
                f'<text x="{x + 14:.0f}" y="{y + 52:.0f}" font-size="10" fill="#7a828f">'
                f"{_esc(_clip(detail, 26))}</text>"
            )
        if node.port_kinds:
            parts.append(_port_kinds_line(x + 14, y + 68, node.port_kinds))
        parts.append("</g>")

    if not simple:
        parts.append(_legend(MARGIN, height - LEGEND_H + 10, width, used_media))
    parts.append("</svg>")
    return "\n".join(parts)


@dataclass
class _Peer:
    """絞り込みの外にいる対向機器を、図の端に置くための簡易ノード。

    拠点で絞ったときに上位ルータが図から消えると、その拠点が
    「どこにも繋がっていない」ように見える。実際には繋がっているので、
    相手がいることだけは残す。ただし本題ではないので、名前と拠点だけに留める。
    """

    x: float
    y: float
    hostname: str
    site_name: str | None
    local_interface: str  # 図の中の機器側のポート
    remote_interface: str  # 相手側のポート
    anchor: int  # 図の中に残っている側の device_id
    confirmed: bool


# 簡易表示の箱。本題ではないので、機器の箱より小さくする
PEER_W = 150.0
PEER_H = 40.0
# 図の中の機器から、どのくらい離して置くか
_PEER_DISTANCE = 120.0


def _boundary_peers(graph: TopologyGraph, keep: set[int]) -> list[_Peer]:
    """片側だけが絞り込みに残った結線から、外側の相手を作る。

    位置は実際の相手の方向へ伸ばした先に置く。図の外にある機器を
    そのままの座標で描くと図が無駄に広がるので、距離だけ詰める。
    向きを合わせておけば、どちら側に出ていく回線かは読み取れる。
    """
    positions = {n.device_id: (n.x + NODE_W / 2, n.y + NODE_H / 2) for n in graph.nodes}
    names = {n.device_id: n for n in graph.nodes}

    # 置いた場所を覚えて、簡易ノード同士が重ならないようにする
    placement = svglayout.Placement(
        [
            svglayout.Rect(n.x, n.y, n.x + NODE_W, n.y + NODE_H).grow(8)
            for n in graph.nodes
            if n.device_id in keep
        ]
    )

    out: list[_Peer] = []
    for edge in graph.edges:
        inside, outside = edge.source, edge.target
        local, remote = edge.source_interface, edge.target_interface
        if inside not in keep and outside in keep:
            inside, outside = outside, inside
            local, remote = remote, local
        elif not (inside in keep and outside not in keep):
            continue

        anchor = positions.get(inside)
        target = positions.get(outside)
        peer_node = names.get(outside)
        if anchor is None or target is None or peer_node is None:
            continue

        dx, dy = target[0] - anchor[0], target[1] - anchor[1]
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux, uy = dx / length, dy / length

        spot = _place_peer(anchor, (ux, uy), placement)
        out.append(
            _Peer(
                x=spot[0] - PEER_W / 2,
                y=spot[1] - PEER_H / 2,
                hostname=peer_node.hostname,
                site_name=peer_node.site_name,
                local_interface=local,
                remote_interface=remote,
                anchor=inside,
                confirmed=edge.confirmed_both_ways,
            )
        )
    return out


def _place_peer(
    anchor: tuple[float, float], direction: tuple[float, float], placement: svglayout.Placement
) -> tuple[float, float]:
    """簡易ノードの中心を決める。相手の方向を保ったまま、空いている場所を探す。"""
    ux, uy = direction
    candidates: list[tuple[float, float]] = []
    for distance in (_PEER_DISTANCE, _PEER_DISTANCE + 70, _PEER_DISTANCE + 150):
        for turn in (0.0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05):
            rx = ux * cos(turn) - uy * sin(turn)
            ry = ux * sin(turn) + uy * cos(turn)
            candidates.append((anchor[0] + rx * distance, anchor[1] + ry * distance))

    rects = [
        svglayout.Rect(cx - PEER_W / 2, cy - PEER_H / 2, cx + PEER_W / 2, cy + PEER_H / 2).grow(10)
        for cx, cy in candidates
    ]
    index, _ = placement.choose(rects)
    placement.add(rects[index])
    return candidates[index]


_ROUTE_CLEAR = 16.0  # 迂回するときに箱から離す距離
_SAME_ROW = 60.0
# 一段下（または上）。親の配下は、横にずれていても上下で結ぶ
_TIER = 100.0
# 右の列（拠点の対向）。1列分だとコア横のアクセスまで横扱いになり箱を貫く
_COLUMN = 400.0


def _side_toward(
    here: tuple[float, float, float, float], there: tuple[float, float, float, float]
) -> str:
    """箱のどの辺から線を出すか。画面側の sideToward と同じ判断。"""
    hx, hy, hw, hh = here
    tx, ty, tw, th = there
    dx = (tx + tw / 2) - (hx + hw / 2)
    dy = (ty + th / 2) - (hy + hh / 2)
    if abs(dy) < _SAME_ROW:
        return "right" if dx > 0 else "left"
    if abs(dx) >= _COLUMN:
        return "right" if dx > 0 else "left"
    if abs(dy) >= _TIER:
        return "bottom" if dy > 0 else "top"
    return "bottom" if dy > 0 else "top"


def _anchor(box: tuple[float, float, float, float], side: str, t: float) -> tuple[float, float]:
    x, y, w, h = box
    t = min(0.85, max(0.15, t))
    if side == "right":
        return (x + w, y + h * t)
    if side == "left":
        return (x, y + h * t)
    if side == "bottom":
        return (x + w * t, y + h)
    return (x + w * t, y)


def _orthogonal_bus(
    source: tuple[float, float],
    source_face: str,
    target: tuple[float, float],
    target_face: str,
    rank: int,
    count: int,
) -> list[tuple[float, float]] | None:
    """縦横だけの通り道。出口から相手の高さへ直角に扇を開く。"""
    gap = 16.0
    inset = 28.0
    rank = max(0, rank)
    steps = max(0, count - 1)
    sx, sy = source
    tx, ty = target
    if source_face == "right" and target_face == "left" and tx - sx > 40:
        usable = max(0.0, tx - sx - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_x = tx - inset - rank * pitch
        return [(sx, sy), (bus_x, sy), (bus_x, ty), (tx, ty)]
    if source_face == "left" and target_face == "right" and sx - tx > 40:
        usable = max(0.0, sx - tx - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_x = tx + inset + rank * pitch
        return [(sx, sy), (bus_x, sy), (bus_x, ty), (tx, ty)]
    if source_face == "bottom" and target_face == "top" and ty - sy > 40:
        usable = max(0.0, ty - sy - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_y = ty - inset - rank * pitch
        return [(sx, sy), (sx, bus_y), (tx, bus_y), (tx, ty)]
    if source_face == "top" and target_face == "bottom" and sy - ty > 40:
        usable = max(0.0, sy - ty - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_y = ty + inset + rank * pitch
        return [(sx, sy), (sx, bus_y), (tx, bus_y), (tx, ty)]
    return None


def _face_t(
    box: tuple[float, float, float, float],
    side: str,
    other: tuple[float, float, float, float],
) -> float:
    """相手の中心を辺へ射影した比率。接続点は辺の上で動かせる。"""
    x, y, w, h = box
    ox, oy, ow, oh = other
    if side in ("left", "right"):
        return ((oy + oh / 2) - y) / max(h, 1.0)
    return ((ox + ow / 2) - x) / max(w, 1.0)


def _space_face_ts(ideals: list[float], face_px: float) -> list[float]:
    """同じ辺の接続点を、重ならないよう広げる。

    対向が箱の外にいると射影が端に潰れる。そのときは辺全体へ均等に振る。
    ポート名が被らないよう、最低でも約 16px 相当の間隔を取る。
    """
    if not ideals:
        return []
    lo, hi = 0.08, 0.92
    n = len(ideals)
    if n == 1:
        return [min(hi, max(lo, ideals[0]))]
    ordered = sorted(
        ((origin, min(hi, max(lo, t))) for origin, t in enumerate(ideals)),
        key=lambda item: (item[1], item[0]),
    )
    minimum = max(0.06, 16.0 / max(face_px, 1.0))
    ideal_span = ordered[-1][1] - ordered[0][1]
    need = minimum * (n - 1)
    if ideal_span < need * 0.85:
        even = [lo + i * (hi - lo) / (n - 1) for i in range(n)]
        result = [0.5] * n
        for (origin, _), value in zip(ordered, even, strict=False):
            result[origin] = value
        return result
    spaced = [t for _, t in ordered]
    for index in range(1, len(spaced)):
        if spaced[index] - spaced[index - 1] < minimum:
            spaced[index] = spaced[index - 1] + minimum
    if spaced[-1] > hi:
        shift = spaced[-1] - hi
        spaced = [max(lo, t - shift) for t in spaced]
        for index in range(1, len(spaced)):
            if spaced[index] - spaced[index - 1] < minimum:
                spaced[index] = spaced[index - 1] + minimum
    result = [0.5] * len(ideals)
    for (origin, _), value in zip(ordered, spaced, strict=False):
        result[origin] = value
    return result


def _wire_path(
    src_box: tuple[float, float, float, float],
    dst_box: tuple[float, float, float, float],
    src_side: str,
    dst_side: str,
    rank: int,
    count: int,
    obstacles: list[svglayout.Rect],
    *,
    src_t: float | None = None,
    dst_t: float | None = None,
) -> list[tuple[float, float]]:
    if src_t is None:
        src_t = 0.5 if count <= 1 else 0.25 + 0.5 * (rank / max(count - 1, 1))
    if dst_t is None:
        dst_t = _face_t(dst_box, dst_side, src_box)
    start = _anchor(src_box, src_side, src_t)
    end = _anchor(dst_box, dst_side, dst_t)
    candidates: list[list[tuple[float, float]]] = []
    bus = _orthogonal_bus(start, src_side, end, dst_side, rank, count)
    if bus and _residual(bus, obstacles) == 0:
        # 通れる扇ならそれを採る。直角の短い折れに負けて箱の縁を走るのを防ぐ。
        return bus
    if bus:
        candidates.append(bus)
    manhattan = _manhattan(start, end, src_side, dst_side)
    candidates.append(manhattan)
    routed = _route(start, end, obstacles)
    if _is_orthogonal(routed):
        candidates.append(routed)
    # 箱を貫く線より、遠回りでも空いている縦横を採る。
    # 同じ通れるなら、折れが少ない方を採る（対向のずれで遠回りしない）。
    return min(
        candidates,
        key=lambda route: (_residual(route, obstacles), len(route), _length(route)),
    )


def _manhattan(
    source: tuple[float, float],
    target: tuple[float, float],
    source_face: str,
    target_face: str,
) -> list[tuple[float, float]]:
    """バスが使えないときも縦横だけ。画面側の manhattanRoute と同じ形。"""
    out = 16.0
    sx, sy = source
    tx, ty = target
    ox = sx + (out if source_face == "right" else -out if source_face == "left" else 0.0)
    oy = sy + (out if source_face == "bottom" else -out if source_face == "top" else 0.0)
    ix = tx + (out if target_face == "right" else -out if target_face == "left" else 0.0)
    iy = ty + (out if target_face == "bottom" else -out if target_face == "top" else 0.0)
    horizontal_first = source_face in ("left", "right")
    mid = (ox, iy) if horizontal_first else (ix, oy)
    points = [source, (ox, oy), mid, (ix, iy), target]
    out_pts = [points[0]]
    for point in points[1:]:
        if abs(point[0] - out_pts[-1][0]) > 0.5 or abs(point[1] - out_pts[-1][1]) > 0.5:
            out_pts.append(point)
    return out_pts if len(out_pts) > 1 else [source, target]


def _ell(
    p1: tuple[float, float], p2: tuple[float, float], horizontal_first: bool
) -> list[tuple[float, float]]:
    mid = (p2[0], p1[1]) if horizontal_first else (p1[0], p2[1])
    if abs(mid[0] - p1[0]) < 0.5 and abs(mid[1] - p1[1]) < 0.5:
        return [p1, p2]
    if abs(mid[0] - p2[0]) < 0.5 and abs(mid[1] - p2[1]) < 0.5:
        return [p1, p2]
    return [p1, mid, p2]


def _is_orthogonal(points: list[tuple[float, float]]) -> bool:
    return all(
        abs(a[0] - b[0]) <= 0.5 or abs(a[1] - b[1]) <= 0.5
        for a, b in zip(points, points[1:], strict=False)
    )


def _route(
    p1: tuple[float, float],
    p2: tuple[float, float],
    obstacles: list[svglayout.Rect],
    depth: int = 0,
) -> list[tuple[float, float]]:
    """関係の無い機器の箱を避けて通る折れ線を返す。斜めは出さない。

    直線で結ぶと、間にある機器の上を通ってしまうことがある。図の上では
    「通過」と「接続」を見分けられないので、その機器にも繋がっていると
    読まれてしまう。専用線が FW を貫いて見えるのがこれ。

    迂回は上下左右の4方向を試し、他の箱にも当たらず、遠回りの少ないものを採る。
    どうしても避けられない場合も縦横の折れ線のまま出す。
    """
    aligned = abs(p1[0] - p2[0]) <= 0.5 or abs(p1[1] - p2[1]) <= 0.5
    if aligned:
        blockers = [rect for rect in obstacles if svglayout.segment_hits(rect, p1, p2)]
        if not blockers:
            return [p1, p2]
    else:
        options = [_ell(p1, p2, True), _ell(p1, p2, False)]
        clear = [route for route in options if _residual(route, obstacles) == 0]
        if clear:
            return min(clear, key=_length)
        blockers = [
            rect
            for rect in obstacles
            for route in options
            for a, b in zip(route, route[1:], strict=False)
            if svglayout.segment_hits(rect, a, b)
        ]
        if not blockers:
            return options[0]

    if depth >= 3:
        options = [_ell(p1, p2, True), _ell(p1, p2, False)]
        return min(options, key=lambda route: (_residual(route, obstacles), _length(route)))

    # 邪魔をしている箱をまとめて1つの塊として避ける。1個ずつ避けると、
    # 避けた先にまた次の箱がいて、堂々巡りになる
    hit = svglayout.Rect(
        min(rect.x1 for rect in blockers),
        min(rect.y1 for rect in blockers),
        max(rect.x2 for rect in blockers),
        max(rect.y2 for rect in blockers),
    )
    mid_x, mid_y = (hit.x1 + hit.x2) / 2, (hit.y1 + hit.y2) / 2
    left, right = hit.x1 - _ROUTE_CLEAR, hit.x2 + _ROUTE_CLEAR
    top, bottom = hit.y1 - _ROUTE_CLEAR, hit.y2 + _ROUTE_CLEAR
    # 辺の外側。角は斜めになるので使わない
    candidates = [
        (mid_x, top),
        (mid_x, bottom),
        (left, mid_y),
        (right, mid_y),
    ]
    # 候補ごとに実際に両側を引き直してから選ぶ。
    # 「この方向なら空いている」を1手だけ見て決めると、避けた先にまた箱がいる
    best: list[tuple[float, float]] | None = None
    best_cost: tuple[int, float] | None = None
    for point in candidates:
        route = _route(p1, point, obstacles, depth + 1)[:-1] + _route(
            point, p2, obstacles, depth + 1
        )
        cost = (_residual(route, obstacles), _length(route))
        if best_cost is None or cost < best_cost:
            best, best_cost = route, cost
        if cost[0] == 0 and depth > 0:
            break  # 奥の階層では、通れる道が見つかった時点で十分

    points = best or _ell(p1, p2, True)

    # 同じ点が続くと、そこだけ向きが決まらない。折れ線から落とす
    out = [points[0]]
    for point in points[1:]:
        if abs(point[0] - out[-1][0]) > 0.5 or abs(point[1] - out[-1][1]) > 0.5:
            out.append(point)
    return out if len(out) > 1 else _ell(p1, p2, True)


def _residual(route: list[tuple[float, float]], obstacles: list[svglayout.Rect]) -> int:
    """引き直しても、まだ箱を通ってしまっている区間の数。"""
    return sum(
        1
        for a, b in zip(route, route[1:], strict=False)
        for rect in obstacles
        if svglayout.segment_hits(rect, a, b)
    )


def _length(route: list[tuple[float, float]]) -> float:
    return sum(
        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        for a, b in zip(route, route[1:], strict=False)
    )


def _path_points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.0f},{y:.0f}" for x, y in points)


def _interior_cross(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> tuple[float, float] | None:
    """直角に交わる線の内側の交点。端で接する重なりは隙間にしない。"""
    ah = abs(a1[1] - a2[1]) <= 0.5
    av = abs(a1[0] - a2[0]) <= 0.5
    bh = abs(b1[1] - b2[1]) <= 0.5
    bv = abs(b1[0] - b2[0]) <= 0.5
    if ah and bv:
        x, y = b1[0], a1[1]
        if (
            min(a1[0], a2[0]) + 2 < x < max(a1[0], a2[0]) - 2
            and min(b1[1], b2[1]) + 2 < y < max(b1[1], b2[1]) - 2
        ):
            return (x, y)
    if av and bh:
        x, y = a1[0], b1[1]
        if (
            min(b1[0], b2[0]) + 2 < x < max(b1[0], b2[0]) - 2
            and min(a1[1], a2[1]) + 2 < y < max(a1[1], a2[1]) - 2
        ):
            return (x, y)
    return None


def _cut_segment(
    p1: tuple[float, float],
    p2: tuple[float, float],
    holes: list[tuple[float, float]],
    radius: float,
) -> list[list[tuple[float, float]]]:
    if not holes:
        return [[p1, p2]]
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    ux, uy = dx / length, dy / length

    def along(point: tuple[float, float]) -> float:
        return (point[0] - p1[0]) * ux + (point[1] - p1[1]) * uy

    ordered = sorted(holes, key=along)
    pieces: list[list[tuple[float, float]]] = []
    cursor = p1
    for hole in ordered:
        t = along(hole)
        stop = (p1[0] + ux * max(0.0, t - radius), p1[1] + uy * max(0.0, t - radius))
        start = (p1[0] + ux * min(length, t + radius), p1[1] + uy * min(length, t + radius))
        if along(stop) - along(cursor) > 1:
            pieces.append([cursor, stop])
        cursor = start
    if along(p2) - along(cursor) > 1:
        pieces.append([cursor, p2])
    return pieces or [[p1, p2]]


def _cut_gaps(
    wires: list[list[tuple[float, float]]], *, radius: float = 5.0
) -> list[list[list[tuple[float, float]]]]:
    """後から描く線が先の線と直角に交わるとき、弧ではなく隙間を空ける。"""
    out: list[list[list[tuple[float, float]]]] = []
    earlier: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def near(p: tuple[float, float], q: tuple[float, float]) -> bool:
        return abs(p[0] - q[0]) <= 0.5 and abs(p[1] - q[1]) <= 0.5

    for wire in wires:
        pieces: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = [wire[0]]
        for a, b in zip(wire, wire[1:], strict=False):
            holes = [
                cross
                for seg in earlier
                if (cross := _interior_cross(a, b, seg[0], seg[1])) is not None
            ]
            chunks = _cut_segment(a, b, holes, radius)
            for index, chunk in enumerate(chunks):
                # 角の手前で切ったあとに次の辺をそのまま足すと斜めになる。
                # 切れた端と次の始点が接しているときだけつなぐ。
                if index == 0 and current and near(current[-1], chunk[0]):
                    current.extend(chunk[1:])
                else:
                    if len(current) >= 2:
                        pieces.append(current)
                    current = list(chunk)
            if current and not near(current[-1], b):
                if len(current) >= 2:
                    pieces.append(current)
                current = []
        if len(current) >= 2:
            pieces.append(current)
        out.append(pieces or [wire])
        earlier.extend(zip(wire, wire[1:], strict=False))
    return out


def _path_middle(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """折れ線の真ん中にあたる区間を返す。中央ラベルの置き場所に使う。"""
    index = max(len(points) // 2 - 1, 0)
    (x1, y1), (x2, y2) = points[index], points[index + 1]
    return x1, y1, x2, y2


def _parallel_index(graph: TopologyGraph) -> dict[int, float]:
    """同じ機器同士を結ぶ線を、平行にずらす量を決める。

    中心と中心を直線で結ぶだけだと、2本目以降が1本目に完全に重なる。
    図の上では1本に見えるのに、実際は2本あるという読み違いを生む。
    LAG や冗長リンクでは普通に起きる。
    """
    groups: dict[tuple[int, int], list[int]] = {}
    for edge in graph.edges:
        key = (min(edge.source, edge.target), max(edge.source, edge.target))
        groups.setdefault(key, []).append(edge.id)

    out: dict[int, float] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        # 束の中央を元の線の位置に保つ
        center = (len(members) - 1) / 2
        for index, edge_id in enumerate(members):
            out[edge_id] = (index - center) * _PARALLEL_GAP
    return out


def _peer_box(x: float, y: float, peer: _Peer, simple: bool) -> str:
    """絞り込みの外にいる相手を、簡易表示で描く。

    破線の枠と控えめな色にして、この図の対象ではないことを見た目で示す。
    実線の箱と同じ見た目にすると、その拠点の機器だと読まれてしまう。
    """
    label = _clip(peer.hostname, 20)
    detail = peer.site_name or "図の対象外"
    if peer.remote_interface and not simple:
        detail = f"{detail} / {portlabel.format(peer.remote_interface, 'short')}"

    return (
        f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{PEER_W:.0f}" height="{PEER_H:.0f}" '
        f'rx="6" fill="#f2f4f7" stroke="#9aa4b2" stroke-width="1.4" stroke-dasharray="5 3"/>'
        f'<text x="{x + PEER_W / 2:.0f}" y="{y + 18:.0f}" font-size="11" font-weight="700" '
        f'fill="#4a5364" text-anchor="middle">{_esc(label)}</text>'
        f'<text x="{x + PEER_W / 2:.0f}" y="{y + 31:.0f}" font-size="9" fill="#7a828f" '
        f'text-anchor="middle">{_esc(_clip(detail, 26))}</text></g>'
    )


def _port_kinds_line(x: float, y: float, kinds: list[str]) -> str:
    """機器の箱に、その機器で使われているポートの種類を書く。

    線のラベルを番号だけにすると何のポートか分からなくなる。色でも表せるが、
    色は凡例を探しに行かないと読めない。機器の隣に名前があれば視線が動かない。
    ラベルと同じ色を使うので、種類が複数あっても線と対応が付く。
    """
    spans: list[str] = []
    budget = 36  # 箱の幅（190px）に 9px の文字で収まるおおよその数
    for index, kind in enumerate(kinds):
        if budget <= 0:
            spans.append('<tspan fill="#7a828f"> ほか</tspan>')
            break
        color = MEDIA_COLOR.get(portlabel.media_of(kind), "#5a6270")
        separator = " / " if index else ""
        text = _clip(kind, budget)
        budget -= len(text) + len(separator)
        spans.append(f'<tspan fill="{color}">{_esc(separator + text)}</tspan>')

    return f'<text x="{x:.0f}" y="{y:.0f}" font-size="9">{"".join(spans)}</text>'


# ノード枠から、その先どのくらい離してラベルを置くか
_LABEL_GAP = 10.0
# 線と垂直な向きへのずらし幅。線に文字が重なると両方読めなくなる
_SIDE_OFFSET = 7.0
_LABEL_FONT = 10.0
# 同じ機器同士を結ぶ線を、何 px ずつ離して描くか
_PARALLEL_GAP = 11.0


def _port_label(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    name: str,
    style: str,
    along_line: bool,
    placement: svglayout.Placement,
    *,
    half_w: float | None = None,
    half_h: float | None = None,
) -> str:
    """(x1,y1) 側の機器のポート名を、その機器のすぐ外に置く。

    線の向きは (x1,y1) → (x2,y2)。ノードの矩形と線の交点まで進んでから
    少し外に出した位置に置くので、どのポートがどちらの機器のものかが
    位置だけで分かる。左右・上下の規則を覚える必要が無くなる。

    置き場所は候補を良い順に試し、機器の箱や他のラベルと重ならない場所を採る。
    自動生成の図で文字が被っているのは道具側の不備なので、当たり判定を通す。
    """
    if not name:
        return ""

    dx, dy = x2 - x1, y2 - y1
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)
    ux, uy = dx / length, dy / length

    color = MEDIA_COLOR.get(portlabel.media_of(name), "#5a6270")

    if along_line:
        # 線に沿わせる。ただし上下逆さまにはしない（読めなくなるため半回転させる）
        angle = (degrees(atan2(dy, dx)) + 180) % 360 - 180  # -180〜180 に正規化
        flipped = angle > 90 or angle <= -90
        if flipped:
            angle += -180 if angle > 0 else 180
        anchor = "end" if flipped else "start"
    else:
        angle = 0.0
        # 線が右向きならラベルは右へ、左向きなら左へ出す
        anchor = "start" if ux > 0.2 else ("end" if ux < -0.2 else "middle")

    # 候補を良い順に並べる。線に沿って少しずつ機器から離し、
    # それぞれについて線の左右と、線から離す量を変える。
    # 最初のものが本来置きたい位置で、後ろほど妥協した位置になる。
    #
    # 線の中点は越えない。 越えると相手側の機器の方が近くなり、
    # 「どちらのポートか」が位置で分からなくなる。それでは元も子もない
    base = _exit_distance(ux, uy, half_w=half_w, half_h=half_h) + _LABEL_GAP
    # 上下方向へ出すとき、文字の上端が枠へ 1px 戻ることがあるので少し足す
    if abs(ux) < 0.25:
        base += 4.0
    limit = length * 0.45
    # 中心→出口だけの距離だと limit が短く追加候補を置けない。
    # 出口の先（枠の外）へ出す候補を、線に沿って必ず数点足す。
    exit_at = _exit_distance(ux, uy, half_w=half_w, half_h=half_h)
    spots = [
        (
            x1 + ux * (base + step) - uy * offset * side,
            y1 + uy * (base + step) + ux * offset * side,
        )
        for step, offset, side in _candidate_offsets(base, max(limit, base + 1))
    ]
    for outward in (6.0, 14.0, 24.0):
        along = exit_at + _LABEL_GAP + outward
        for offset, side in (
            (_SIDE_OFFSET, 1.0),
            (_SIDE_OFFSET, -1.0),
            (_SIDE_OFFSET + 11.0, 1.0),
            (_SIDE_OFFSET + 11.0, -1.0),
        ):
            spots.append(
                (
                    x1 + ux * along - uy * offset * side,
                    y1 + uy * along + ux * offset * side,
                )
            )

    # 置けないときは文字を短くする。重ねるくらいなら略した方がまし。
    # 略した場合は元の名前を title に入れて、画面で開けば読めるようにする
    chosen: tuple[str, int, svglayout.Rect] | None = None
    fallback: tuple[str, int, svglayout.Rect] | None = None
    for text in _label_variants(name, style):
        rects = [
            svglayout.text_rect(px, py - 3, text, _LABEL_FONT, anchor, angle) for px, py in spots
        ]
        index, fits = placement.choose(rects)
        if fits:
            chosen = (text, index, rects[index])
            break
        if fallback is None:
            fallback = (text, index, rects[index])

    text, index, rect = chosen or fallback  # type: ignore[misc]
    px, py = spots[index]
    # 出力時に座標を丸める分だけ余裕を持たせる。ぎりぎりで置くと、
    # 丸めた結果わずかに重なることがある
    placement.add(rect.grow(1.0))

    transform = f' transform="rotate({angle:.1f} {px:.1f} {py:.1f})"' if angle else ""
    title = f"<title>{_esc(name)}</title>" if text != name else ""
    return (
        f'<text class="port-label" x="{px:.1f}" y="{py - 3:.1f}" '
        f'font-size="{_LABEL_FONT:.0f}" fill="{color}" '
        f'text-anchor="{anchor}" paint-order="stroke" stroke="#ffffff" '
        f'stroke-width="3"{transform}>{title}{_esc(text)}</text>'
    )


def _candidate_offsets(base: float, limit: float) -> list[tuple[float, float, float]]:
    """ラベルを置く候補を（線に沿う距離, 線から離す量, 左右）で並べる。

    近いほど良いので、2つのずらし量の合計が小さい順にする。1本の線に何本も
    束ねている構成（LAG など）では同じ場所に何個も置くことになるため、
    線に沿う方向と垂直な方向の両方に逃がせないと必ずどこかで被る。
    """
    steps = [0.0, 14.0, 28.0, 44.0, 62.0, 84.0, 110.0]
    offsets = [_SIDE_OFFSET + 11.0 * index for index in range(12)]

    out: list[tuple[float, float, float, float]] = []
    for step_index, step in enumerate(steps):
        # 中点を越えると相手側の機器の方が近くなり、どちらのポートか分からなくなる
        if step and base + step > limit:
            break
        for offset_index, offset in enumerate(offsets):
            for side in (1.0, -1.0):
                rank = step_index + offset_index * 1.2
                out.append((rank, step, offset, side))

    out.sort(key=lambda item: item[0])
    return [(step, offset, side) for _, step, offset, side in out]


def _label_variants(name: str, style: str) -> list[str]:
    """指定された表記から順に、短くしていく候補。

    置き場所が無いときに重ねてしまうと図が読めなくなる。
    入りきらないなら略す方を選ぶ。指定より長くはしない。
    """
    order = ["full", "short", "number"]
    start = order.index(style) if style in order else 1

    out: list[str] = []
    for candidate in order[start:]:
        text = _clip(portlabel.format(name, candidate), 30 if candidate == "full" else 18)
        if text and text not in out:
            out.append(text)

    # それでも入らない場合の最後の手段。番号の末尾だけ残す
    tail = portlabel.split(name)[1]
    if tail:
        short = tail.rsplit("/", 1)[-1]
        if short not in out:
            out.append(short)
    return out


def _exit_distance(
    ux: float, uy: float, *, half_w: float | None = None, half_h: float | None = None
) -> float:
    """ノードの中心から、その向きで枠を出るまでの距離。"""
    hw = NODE_W / 2 if half_w is None else half_w
    hh = NODE_H / 2 if half_h is None else half_h
    tx = hw / abs(ux) if abs(ux) > 1e-6 else float("inf")
    ty = hh / abs(uy) if abs(uy) > 1e-6 else float("inf")
    return min(tx, ty)


def _center_label(
    x1: float, y1: float, x2: float, y2: float, label: str, placement: svglayout.Placement
) -> str:
    """人が付けた線の名前。線の中ほどに置き、空いているところを探す。"""
    text = _clip(label, 28)
    spots = [
        (
            x1
            + (x2 - x1) * along
            - (y2 - y1) / max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0) * off,
            y1
            + (y2 - y1) * along
            + (x2 - x1) / max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0) * off,
        )
        for along in (0.5, 0.38, 0.62, 0.28, 0.72)
        for off in (0.0, -13.0, 13.0)
    ]
    rects = [svglayout.text_rect(px, py - 4, text, _LABEL_FONT, "middle") for px, py in spots]
    index, _ = placement.choose(rects)
    px, py = spots[index]
    placement.add(rects[index].grow(1.0))

    return (
        f'<text class="edge-label" x="{px:.0f}" y="{py - 4:.0f}" '
        f'font-size="{_LABEL_FONT:.0f}" fill="#5a6270" '
        f'text-anchor="middle" paint-order="stroke" stroke="#ffffff" '
        f'stroke-width="3">{_esc(text)}</text>'
    )


def _legend(x: float, y: float, width: float, media: set[Media] | None = None) -> str:
    parts = [
        f'<line x1="{x:.0f}" y1="{y - 12:.0f}" x2="{width - x:.0f}" y2="{y - 12:.0f}" '
        f'stroke="#dfe3ea" stroke-width="1"/>',
        f'<text x="{x:.0f}" y="{y + 4:.0f}" font-size="10" fill="#6b6b6b">保守・EoL の状態</text>',
    ]
    cursor = x + 96
    for key, label in SEVERITY_LABEL.items():
        color = SEVERITY_COLOR[key]
        parts.append(
            f'<rect x="{cursor:.0f}" y="{y - 5:.0f}" width="10" height="10" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{cursor + 15:.0f}" y="{y + 4:.0f}" font-size="10" fill="#4a4a4a">'
            f"{_esc(label)}</text>"
        )
        cursor += 22 + len(label) * 11

    parts.append(
        f'<text x="{x:.0f}" y="{y + 24:.0f}" font-size="10" fill="#6b6b6b">'
        "実線 = 両側の機器から確認できた結線 / 破線 = 片側のみ</text>"
    )

    # ポート名を色分けしているので、何色が何かを図の中に残す。
    # 凡例の無い色分けは、その図だけ見た人には読めない
    if media:
        cursor = x + 250
        parts.append(
            f'<text x="{cursor:.0f}" y="{y + 24:.0f}" font-size="10" fill="#6b6b6b">'
            "ポート種別</text>"
        )
        cursor += 60
        for item in sorted(media, key=lambda m: list(MEDIA_LABEL).index(m)):
            label = MEDIA_LABEL[item]
            parts.append(
                f'<text x="{cursor:.0f}" y="{y + 24:.0f}" font-size="10" '
                f'fill="{MEDIA_COLOR[item]}" font-weight="700">{_esc(label)}</text>'
            )
            cursor += 18 + len(label) * 8

    return "\n".join(parts)


def _empty_svg(title: str, *, layer: str = "physical") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="520" height="120" '
        f'viewBox="0 0 520 120" font-family="{FONT}">'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<text x="24" y="42" font-size="16" font-weight="700">{_esc(title)}</text>'
        '<text x="24" y="70" font-size="12" fill="#6b6b6b">'
        "表示できる機器がありません。Config を取り込むと結線が推定されます。</text>"
        f'<text x="24" y="94" font-size="11" fill="#6b6b6b">レイヤ: {_esc(layer)}</text>'
        "</svg>"
    )


def export_svg(
    db: Session,
    layer: str,
    *,
    title: str = "ネットワーク構成図",
    vlan: int | None = None,
    vrf: str | None = None,
    site_id: int | None = None,
    style: str = "detail",
    port_style: str = "short",
    along_line: bool = False,
    show_peers: bool = True,
    orientation: str = "vertical",
) -> str:
    return render_svg(
        load_diagram(db, layer, site_id=site_id, orientation=orientation),
        title=title,
        vlan=vlan,
        vrf=vrf,
        site_id=site_id,
        style=style,
        port_style=port_style,
        along_line=along_line,
        show_peers=show_peers,
    )
