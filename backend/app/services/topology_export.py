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
    _DETAIL_HEIGHT,
    PLANE_NAMES,
    TopologyGraph,
    _box_height,
    _box_width,
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
#: 機器の種別の呼び方。画面（DEVICE_TYPE_LABEL）と揃えること
DEVICE_TYPE_NAMES: dict[str, str] = {
    "router": "ルータ",
    "switch": "スイッチ",
    "firewall": "ファイアウォール",
    "load_balancer": "ロードバランサ",
    "wireless_ap": "無線AP",
    "wireless_controller": "無線コントローラ",
    "server": "サーバ",
    "other": "その他",
}
#: 台帳の稼働状態の呼び方
DEVICE_STATUS_NAMES: dict[str, str] = {
    "active": "稼働中",
    "standby": "予備機",
    "maintenance": "保守中",
    "retired": "撤去済み",
}
#: 種別の記号。画面の TYPE_GLYPH と揃えること
TYPE_GLYPH: dict[str, str] = {
    "router": "◇",
    "switch": "▭",
    "firewall": "🛡",
    "load_balancer": "⇄",
    "wireless_ap": "((•))",
    "wireless_controller": "◎",
    "server": "▤",
    "other": "▪",
}
SEVERITY_LABEL: dict[str, str] = {
    str(Severity.EXPIRED): "期限切れ",
    str(Severity.CRITICAL): "90日以内",
    str(Severity.WARNING): "1年以内",
    str(Severity.OK): "余裕あり",
    str(Severity.UNKNOWN): "情報未登録",
}

NODE_W = 210.0
# ポートの種類（GigabitEthernet など）を箱の中に1行入れる分の高さを含む。
# 線のラベルを番号だけにしても、機器を見れば何のポートか分かるようにするため
NODE_H = 78.0
MARGIN = 48.0
LEGEND_H = 62.0
# 1本あたりの折れの上限。同じ段の迂回で2つ使う（品質検査と合わせること）
MAX_BENDS = 2
# 箱の中の行送り（画面の .topo-node と同じ積み方）
_ROW_HEAD = 20.0
_ROW_SUB = 15.0
_ROW_BODY = 16.0
_ROW_KINDS = 15.0
# ポート名が縦に積まれても重ならない間隔（画面の LABEL_PITCH と同じ）
_LABEL_PITCH = 18.0
# 同じ相手への束をどこまで詰められるか。上下の辺は名前を横書きするので
# 名前の幅がいる。左右の辺は行の高さ（_LABEL_PITCH）で決まる
_BUNDLE_LABEL_W = 56.0

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

    # 画面の assignAnchors と同じ手順。
    # 1) まず基準高さの箱で口を決め、2) 左右の本数で高さを伸ばす。
    # sideToward のまま数えると、あとで上下へ付け替えた線まで左右本数に入り、
    # 画面より高い箱になる。
    base_h = NODE_H if simple else _DETAIL_HEIGHT

    def box_at(node, height: float, width: float) -> tuple[float, float, float, float]:
        shift_x = (width - NODE_W) / 2
        shift_y = (height - NODE_H) / 2
        return (node.x - shift_x, node.y - shift_y, width, height)

    provisional = {n.device_id: box_at(n, base_h, NODE_W) for n in nodes}
    per_side: dict[tuple[int, str], int] = {}
    for edge in graph.edges:
        if edge.source not in provisional or edge.target not in provisional:
            continue
        others = [
            _xywh_bounds(provisional[device_id])
            for device_id in provisional
            if device_id not in (edge.source, edge.target)
        ]
        src_side, dst_side = _faces_for_edge(
            provisional[edge.source],
            provisional[edge.target],
            others,
            src_side=edge.source_side,
            dst_side=edge.target_side,
        )
        per_side[(edge.source, src_side)] = per_side.get((edge.source, src_side), 0) + 1
        per_side[(edge.target, dst_side)] = per_side.get((edge.target, dst_side), 0) + 1
    busiest_tb: dict[int, int] = {}
    busiest_lr: dict[int, int] = {}
    for (device_id, side), count in per_side.items():
        if side in ("left", "right"):
            busiest_lr[device_id] = max(busiest_lr.get(device_id, 0), count)
        else:
            busiest_tb[device_id] = max(busiest_tb.get(device_id, 0), count)

    heights = {n.device_id: _box_height(busiest_lr.get(n.device_id, 0), base_h) for n in nodes}

    def display_box(node) -> tuple[float, float, float, float]:
        """保存左上を、広げた箱の中心が保たれる画面座標の箱へ。"""
        w = _box_width(busiest_tb.get(node.device_id, 0))
        h = heights.get(node.device_id, base_h)
        return box_at(node, h, w)

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

    at = {n.device_id: (sized[n.device_id][0] + dx, sized[n.device_id][1] + dy) for n in nodes}
    size_at = {n.device_id: (sized[n.device_id][2], sized[n.device_id][3]) for n in nodes}
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
        others = [
            _xywh_bounds(boxes_by_id[device_id])
            for device_id in boxes_by_id
            if device_id not in (edge.source, edge.target)
        ]
        src_side, dst_side = _faces_for_edge(
            src_box,
            dst_box,
            others,
            src_side=edge.source_side,
            dst_side=edge.target_side,
        )
        sides[edge.id] = (src_side, dst_side)
        src_along = dst_box[1] if src_side in ("left", "right") else dst_box[0]
        dst_along = src_box[1] if dst_side in ("left", "right") else src_box[0]
        groups.setdefault((edge.source, src_side), []).append((src_along, edge.id))
        groups.setdefault((edge.target, dst_side), []).append((dst_along, edge.id))

    # 図の外の対向へ出る線も、同じ辺の口を分け合う。辺の中央に固定したままだと、
    # 同じ辺から出ている結線と重なり、線どうしが交差する。
    # 結線の id と混ざらないよう、対向には負の番号を振る
    peer_boxes_by_key: dict[int, tuple[float, float, float, float]] = {}
    peer_key_of: dict[int, int] = {}
    peer_sides: dict[int, str] = {}
    for index, peer in enumerate(peers):
        anchor = at.get(peer.anchor)
        if anchor is None:
            continue
        key = -(index + 1)
        peer_key_of[index] = key
        anchor_box = boxes_by_id[peer.anchor]
        peer_box = (peer.x + dx, peer.y + dy, PEER_W, PEER_H)
        peer_boxes_by_key[key] = peer_box
        side = _side_toward(anchor_box, peer_box)
        peer_sides[key] = side
        along = peer_box[1] if side in ("left", "right") else peer_box[0]
        groups.setdefault((peer.anchor, side), []).append((along, key))

    lane_rank: dict[int, tuple[int, int]] = {}
    face_t: dict[tuple[int, str, int], float] = {}
    edge_by_id = {edge.id: edge for edge in graph.edges}
    for (device_id, side), members in groups.items():
        members.sort()
        box = boxes_by_id[device_id]
        ideals: list[float] = []
        # 同じ相手行きの束を詰めて置くため、辺ごとの相手を控える
        opposite: list[int] = []
        for _, edge_id in members:
            if edge_id < 0:
                ideals.append(_face_t(box, side, peer_boxes_by_key[edge_id]))
                opposite.append(edge_id)
                continue
            edge = edge_by_id[edge_id]
            other_id = edge.target if edge.source == device_id else edge.source
            ideals.append(_face_t(box, side, boxes_by_id[other_id]))
            opposite.append(other_id)
        for index, (_, edge_id) in enumerate(members):
            lane_rank[edge_id] = (index, len(members))
        vertical_face = side in ("left", "right")
        spaced = _space_face_ts(
            ideals,
            box[3] if vertical_face else box[2],
            opposite,
            horizontal=not vertical_face,
        )
        for (_, edge_id), t in zip(members, spaced, strict=False):
            face_t[(device_id, side, edge_id)] = t

    # --- 結線（ノードより先に描いて背面に回す） ---
    pending: list[tuple[list[tuple[float, float]], object]] = []
    # 引き直し用に、その結線を引いたときの条件を控える
    drawn_with: list[dict] = []
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
            avoid=[route for route, _ in pending],
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
        drawn_with.append(
            {
                "args": (src_box, dst_box, src_side, dst_side, rank_i, rank_n, obstacles),
                "src_t": face_t.get((edge.source, src_side, edge.id)),
                "dst_t": face_t.get((edge.target, dst_side, edge.id)),
            }
        )
        pending.append((points, edge))

    # 引き直し。1本目を引いた時点では、あとから来る線はまだ無い。
    # 全部引き終えてから、交差している線だけ、ほかの線を避けてもう一度引く
    for index, (points, _) in enumerate(pending):
        others = [route for position, (route, _) in enumerate(pending) if position != index]
        if _route_cross_count(points, others) == 0:
            continue
        how = drawn_with[index]
        again = _wire_path(*how["args"], src_t=how["src_t"], dst_t=how["dst_t"], avoid=others)
        if _route_cross_count(again, others) < _route_cross_count(points, others):
            pending[index] = (again, pending[index][1])

    # 同じ辺回りは箱の下を通る。キャンバスを箱だけだと線が凡例や外枠へ食い込む。
    wire_ys = [y for points, _ in pending for _, y in points]
    wire_xs = [x for points, _ in pending for x, _ in points]
    if wire_ys:
        # 凡例（または下余白）の手前に MARGIN 分空ける
        floor = height - legend_h - MARGIN
        ceiling = MARGIN
        right = width - MARGIN
        left = MARGIN
        grow_y = max(0.0, max(wire_ys) - floor) + max(0.0, ceiling - min(wire_ys))
        grow_x = max(0.0, max(wire_xs) - right) + max(0.0, left - min(wire_xs))
        if grow_y or grow_x:
            height += grow_y
            width += grow_x
            for index, part in enumerate(parts):
                if part.startswith("<svg "):
                    parts[index] = (
                        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
                        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
                        f'font-family="{FONT}">'
                    )
                    break
            # 右上の時点ラベルも新しい幅へ
            if grow_x:
                for index, part in enumerate(parts):
                    if 'text-anchor="end"' in part and "時点" in part:
                        parts[index] = part.replace(
                            f'x="{width - grow_x - MARGIN:.0f}"',
                            f'x="{width - MARGIN:.0f}"',
                            1,
                        )
                        break

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
        # 人が付けた線の名前は中央に置く。両端のポート名はそのまま残す。
        # 上（相手側）・中央（名前）・下（自分側）が縦に並んで読めるようにする
        if edge.label:
            parts.append(_center_label(*_path_middle(points), edge.label, placement))
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
    for peer_index, peer in enumerate(peers):
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
        key = peer_key_of.get(peer_index)
        src_side = peer_sides.get(key, "") if key is not None else ""
        if not src_side:
            src_side = _side_toward(src_box, dst_box)
        dst_side = _side_toward(dst_box, src_box)
        points = _wire_path(
            src_box,
            dst_box,
            src_side,
            dst_side,
            0,
            1,
            obstacles,
            src_t=face_t.get((peer.anchor, src_side, key)) if key is not None else None,
            avoid=[route for route, _ in pending],
        )
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

        # 対向は「どこへ出ているか」だけ。先はその拠点の図で追うので、
        # 名前と拠点だけを破線の箱に出す
        if node.outside:
            where = node.site_name or "拠点未登録"
            if node.hidden_peers:
                where = f"{where} ・ほか{node.hidden_peers}台"
            parts.append(
                f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{nw:.0f}" height="{PEER_H:.0f}" '
                f'rx="7" fill="none" stroke="#9aa4b2" stroke-width="1.4" stroke-dasharray="5 3"/>'
                f'<text x="{x + 12:.0f}" y="{y + 22:.0f}" font-size="12" fill="#4a5364">'
                f"{_esc(TYPE_GLYPH.get(str(node.device_type), ''))}</text>"
                f'<text x="{x + 30:.0f}" y="{y + 22:.0f}" font-size="12" font-weight="700" '
                f'fill="#4a5364">{_esc(_clip(node.hostname, 20))}</text>'
                f'<text x="{x + 12:.0f}" y="{y + 38:.0f}" font-size="10" fill="#7a828f">'
                f"{_esc(_clip(where, 26))}</text></g>"
            )
            continue

        color = SEVERITY_COLOR.get(str(node.severity), SEVERITY_COLOR[str(Severity.UNKNOWN)])
        # 背景は稼働状態。止まっているものは灰色寄りにして、稼働中と区別する
        fill = "#f7f9fc" if str(node.status) == "active" else "#eceef1"
        parts.append(
            f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{nw:.0f}" height="{nh:.0f}" '
            f'rx="7" fill="{fill}" stroke="{color}" stroke-width="2"/>'
            # 状態が一目で分かるよう左端に色帯を置く（白黒印刷でも位置で区別できる）
            f'<rect x="{x:.0f}" y="{y:.0f}" width="5" height="{nh:.0f}" '
            f'rx="2" fill="{color}"/>'
        )
        # 中身は画面の箱と同じ順に積む。SVG だけ行が欠けると、図から拠点や
        # 保守の状態が読めなくなり、貼った先で台帳を開き直すことになる。
        # 上段は左に管理ID、右に種別
        row_y = y + 18.0
        type_name = DEVICE_TYPE_NAMES.get(str(node.device_type), "")
        if node.code:
            parts.append(
                f'<text x="{x + 14:.0f}" y="{row_y:.0f}" font-size="9" fill="#8a929e">'
                f"{_esc(_clip(node.code, 16))}</text>"
            )
        if type_name:
            parts.append(
                f'<text x="{x + nw - 12:.0f}" y="{row_y:.0f}" font-size="9" '
                f'font-weight="700" fill="#8a929e" text-anchor="end">{_esc(type_name)}</text>'
            )
        # 機器名（無ければホスト名）。人が付けた呼び名があるときは下にホスト名
        row_y += _ROW_HEAD
        title = node.label or node.hostname
        # 記号は別の text にする。名前だけを取り出せる方が、図を読み直す側
        # （試験や外の道具）で扱いやすい
        parts.append(
            f'<text x="{x + 12:.0f}" y="{row_y:.0f}" font-size="12" fill="#4a5364">'
            f"{_esc(TYPE_GLYPH.get(str(node.device_type), ''))}</text>"
        )
        parts.append(
            f'<text x="{x + 30:.0f}" y="{row_y:.0f}" font-size="13" font-weight="700" '
            f'fill="#1a1a1a">{_esc(_clip(title, 20))}</text>'
        )
        if node.label:
            row_y += _ROW_SUB
            parts.append(
                f'<text x="{x + 28:.0f}" y="{row_y:.0f}" font-size="10" fill="#7a828f">'
                f"{_esc(_clip(node.hostname, 24))}</text>"
            )
        for line in _card_lines(node):
            row_y += _ROW_BODY
            parts.append(
                f'<text x="{x + 14:.0f}" y="{row_y:.0f}" font-size="10" fill="#5a6270">'
                f"{_esc(_clip(line, 28))}</text>"
            )
        if node.port_kinds:
            row_y += _ROW_KINDS
            parts.append(_port_kinds_line(x + 14, row_y, node.port_kinds))
        # 下段は右揃えで、稼働状態と保守状態
        foot = " ・".join(
            part
            for part in (
                DEVICE_STATUS_NAMES.get(str(node.status), ""),
                SEVERITY_LABEL.get(str(node.severity), ""),
            )
            if part
        )
        parts.append(
            f'<text x="{x + nw - 12:.0f}" y="{y + nh - 10:.0f}" font-size="10" '
            f'font-weight="600" fill="{color}" text-anchor="end">{_esc(foot)}</text>'
        )
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
    t = min(0.92, max(0.08, t))
    if side == "right":
        return (x + w, y + h * t)
    if side == "left":
        return (x, y + h * t)
    if side == "bottom":
        return (x + w * t, y + h)
    return (x + w * t, y)


def _lr_path_clear(
    src_box: tuple[float, float, float, float],
    dst_box: tuple[float, float, float, float],
    src_side: str,
    dst_side: str,
    others: list[tuple[float, float, float, float]],
) -> bool:
    """左右の口で直進（または短い扇）できるか。途中の箱を貫くなら False。"""
    start = _anchor(src_box, src_side, 0.5)
    end = _anchor(dst_box, dst_side, 0.5)
    boxes = [_xywh_bounds(src_box), _xywh_bounds(dst_box), *others]
    for route in (
        _orthogonal_bus(start, src_side, end, dst_side, 0, 1, boxes),
        _manhattan(start, end, src_side, dst_side),
    ):
        if not route:
            continue
        if _route_pierce_count(route, boxes) == 0:
            return True
    return False


def _faces_for_edge(
    src_box: tuple[float, float, float, float],
    dst_box: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
    *,
    src_side: str | None = None,
    dst_side: str | None = None,
) -> tuple[str, str]:
    """引き出し辺。左右が塞がる同じ段は上下の同じ辺から回して2折れにする。"""
    src = src_side or _side_toward(src_box, dst_box)
    dst = dst_side or _side_toward(dst_box, src_box)
    if src in ("left", "right") and dst in ("left", "right"):
        if _lr_path_clear(src_box, dst_box, src, dst, others):
            return src, dst
        # 左右が塞がる。段差があるなら上下の対向辺、同じ段なら同じ辺から回す。
        boxes = [_xywh_bounds(src_box), _xywh_bounds(dst_box), *others]
        candidates: list[tuple[float, str, str]] = []
        face_pairs = (("bottom", "top"), ("top", "bottom"), ("bottom", "bottom"), ("top", "top"))
        for face_s, face_t in face_pairs:
            start = _anchor(src_box, face_s, 0.5)
            end = _anchor(dst_box, face_t, 0.5)
            route = _orthogonal_bus(start, face_s, end, face_t, 0, 1, boxes)
            if not route:
                continue
            if _route_pierce_count(route, boxes) > 0:
                continue
            candidates.append((_length(route), face_s, face_t))
        if candidates:
            candidates.sort()
            return candidates[0][1], candidates[0][2]
    return src, dst


def _clear_bus_y(
    bus_y: float,
    sx: float,
    tx: float,
    side: str,
    obstacles: list[tuple[float, float, float, float]] | None,
    *,
    pad: float = 24.0,
) -> float:
    """同じ辺回りの横バスを、通路上の箱の外へ押し出す。

    口のすぐ下だけ回すと、配下への縦線と直角に交差し、隙間切りで切れ端になる。
    横に重なる箱の下（または上）まで逃がせば、2折れのまま交差を減らせる。
    """
    if not obstacles:
        return bus_y
    lo_x, hi_x = (min(sx, tx), max(sx, tx))
    out = bus_y
    for x1, y1, x2, y2 in obstacles:
        if x2 < lo_x - 1 or x1 > hi_x + 1:
            continue
        if side == "below":
            out = max(out, y2 + pad)
        else:
            out = min(out, y1 - pad)
    return out


def _orthogonal_bus(
    source: tuple[float, float],
    source_face: str,
    target: tuple[float, float],
    target_face: str,
    rank: int,
    count: int,
    obstacles: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[float, float]] | None:
    """縦横だけの通り道。出口から相手の高さへ直角に扇を開く。"""
    gap = 16.0
    inset = 28.0
    rank = max(0, rank)
    steps = max(0, count - 1)
    sx, sy = source
    tx, ty = target
    if source_face == "right" and target_face == "left" and tx - sx > 40:
        if abs(sy - ty) < 3:
            return [(sx, sy), (tx, ty)]
        usable = max(0.0, tx - sx - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_x = tx - inset - rank * pitch
        return [(sx, sy), (bus_x, sy), (bus_x, ty), (tx, ty)]
    if source_face == "left" and target_face == "right" and sx - tx > 40:
        if abs(sy - ty) < 3:
            return [(sx, sy), (tx, ty)]
        usable = max(0.0, sx - tx - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_x = tx + inset + rank * pitch
        return [(sx, sy), (bus_x, sy), (bus_x, ty), (tx, ty)]
    if source_face == "bottom" and target_face == "top" and ty - sy > 40:
        if abs(sx - tx) < 3:
            return [(sx, sy), (tx, ty)]
        usable = max(0.0, ty - sy - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_y = ty - inset - rank * pitch
        return [(sx, sy), (sx, bus_y), (tx, bus_y), (tx, ty)]
    if source_face == "top" and target_face == "bottom" and sy - ty > 40:
        if abs(sx - tx) < 3:
            return [(sx, sy), (tx, ty)]
        usable = max(0.0, sy - ty - inset * 2)
        pitch = min(gap, usable / steps) if steps else 0.0
        bus_y = ty + inset + rank * pitch
        return [(sx, sy), (sx, bus_y), (tx, bus_y), (tx, ty)]
    # 同じ段で左右が塞がるとき、上下どちらかの辺から回す（2折れ）
    if source_face == "bottom" and target_face == "bottom":
        bus_y = max(sy, ty) + inset + rank * (gap if steps else 0.0)
        bus_y = _clear_bus_y(bus_y, sx, tx, "below", obstacles)
        return [(sx, sy), (sx, bus_y), (tx, bus_y), (tx, ty)]
    if source_face == "top" and target_face == "top":
        bus_y = min(sy, ty) - inset - rank * (gap if steps else 0.0)
        bus_y = _clear_bus_y(bus_y, sx, tx, "above", obstacles)
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


def _space_face_ts(
    ideals: list[float],
    face_px: float,
    peers: list[int] | None = None,
    *,
    horizontal: bool = False,
) -> list[float]:
    """同じ辺の接続点を並べる（画面の spaceTs と同じ規則）。

    相手の方角へ寄せた位置を軸に、ポート名が被らないだけの間隔を空ける。
    辺いっぱいに散らすと、真っ直ぐ引ける線まで箱の端へ寄って折れができる。
    必要な幅だけ使い、残りは中央へ寄せる。

    同じ相手へ何本も張っている束（ブリッジやポートチャネル）は、間隔を詰めて
    隣どうしに置く。1本ずつ等間隔に離すと、どれが同じ相手行きか分からない。
    """
    if not ideals:
        return []
    lo, hi = 0.08, 0.92
    n = len(ideals)
    if n == 1:
        return [min(hi, max(lo, ideals[0]))]
    peer_of = peers if peers is not None else list(range(n))
    ordered = sorted(
        ((origin, min(hi, max(lo, t)), peer_of[origin]) for origin, t in enumerate(ideals)),
        key=lambda item: (item[1], item[0]),
    )
    minimum = max(0.06, _LABEL_PITCH / max(face_px, 1.0))
    # 束を詰める間隔。上下の辺はポート名を横書きするので、名前の幅がいる。
    # 左右の辺は行の高さで足りるので、ここまで詰められる
    # 束でもポート名は1つずつ読むので、行の高さより詰めることはできない
    bundle = max(minimum, _BUNDLE_LABEL_W / max(face_px, 1.0)) if horizontal else minimum
    gaps = [
        bundle if ordered[index][2] == ordered[index - 1][2] else minimum for index in range(1, n)
    ]
    ideal_span = ordered[-1][1] - ordered[0][1]
    need = sum(gaps)
    result = [0.5] * n

    # 同じ相手へ何本も張っている束（ブリッジやポートチャネル）は、必要な幅だけ
    # 使って射影のまん中に固める。辺いっぱいに散らすと、真っ直ぐ引ける線まで
    # 箱の端へ寄り、束のうちどれが同じ相手行きなのかも分からなくなる。
    # 相手がばらばらの扇は従来どおり辺全体を使う（ポート名を読む幅が要る）
    single_peer = all(item[2] == ordered[0][2] for item in ordered)
    if single_peer:
        if need >= hi - lo:
            # どう詰めても収まらない。せめて等間隔にして被りを最小にする
            for index, (origin, _, _) in enumerate(ordered):
                result[origin] = lo + index * (hi - lo) / (n - 1)
            return result
        wanted = sum(t for _, t, _ in ordered) / n
        center = min(hi - need / 2, max(lo + need / 2, wanted))
        at = center - need / 2
        for index, (origin, _, _) in enumerate(ordered):
            if index:
                at += gaps[index - 1]
            result[origin] = at
        return result

    # 相手がばらばらなのに射影が端に潰れている（対向がみな箱の外）。
    # どれがどこ行きかは辺の上の順で読むしかないので、辺全体へ均等に振る
    if ideal_span < need * 0.85:
        for index, (origin, _, _) in enumerate(ordered):
            result[origin] = lo + index * (hi - lo) / (n - 1)
        return result

    spaced = [t for _, t, _ in ordered]
    for index in range(1, len(spaced)):
        room = gaps[index - 1]
        if spaced[index] - spaced[index - 1] < room:
            spaced[index] = spaced[index - 1] + room
    if spaced[-1] > hi:
        shift = spaced[-1] - hi
        spaced = [max(lo, t - shift) for t in spaced]
        for index in range(1, len(spaced)):
            room = gaps[index - 1]
            if spaced[index] - spaced[index - 1] < room:
                spaced[index] = spaced[index - 1] + room
    for (origin, _, _), value in zip(ordered, spaced, strict=False):
        result[origin] = value
    return result


def _exits_outward(route: list[tuple[float, float]], face: str) -> bool:
    """最初の辺が面の外側へ出ているか。内側へ戻る線は箱を貫く。"""
    if len(route) < 2:
        return False
    x0, y0 = route[0]
    x1, y1 = route[1]
    if face == "left":
        return x1 < x0 - 0.5
    if face == "right":
        return x1 > x0 + 0.5
    if face == "top":
        return y1 < y0 - 0.5
    if face == "bottom":
        return y1 > y0 + 0.5
    return True


def _arrives_from_outside(route: list[tuple[float, float]], face: str) -> bool:
    """最後の辺が面の外側から来ているか。箱の中を通って辺へ着く線は貫き。"""
    if len(route) < 2:
        return False
    x0, y0 = route[-1]
    x1, y1 = route[-2]
    if face == "left":
        return x1 < x0 - 0.5
    if face == "right":
        return x1 > x0 + 0.5
    if face == "top":
        return y1 < y0 - 0.5
    if face == "bottom":
        return y1 > y0 + 0.5
    return True


def _xywh_bounds(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return (x, y, x + w, y + h)


def _segment_pierces_box(
    box: tuple[float, float, float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """箱の内側を通るか。辺上の接続は貫きにしない。

    長い線分だと固定サンプルが薄まり、箱の端をかすめる貫きを見逃す。
    内側矩形との交差区間で判定する。
    """
    x1, y1, x2, y2 = box
    pad = 4.0
    ix1, iy1, ix2, iy2 = x1 + pad, y1 + pad, x2 - pad, y2 - pad
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - ix1), (dx, ix2 - ax), (-dy, ay - iy1), (dy, iy2 - ay)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    # 端点だけの接触は接続。内側を有限の長さで通るときだけ貫き。
    return t1 - t0 > 1e-4 and t0 < 1.0 - 1e-9 and t1 > 1e-9


def _route_pierce_count(
    route: list[tuple[float, float]], boxes: list[tuple[float, float, float, float]]
) -> int:
    """端点の箱も含め、線が箱の中を通る回数。obstacles から端点を外すと見逃す。"""
    return sum(
        1
        for a, b in zip(route, route[1:], strict=False)
        for box in boxes
        if _segment_pierces_box(box, a, b)
    )


def _crosses_at_right_angle(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    """縦の線と横の線が、互いの内側で交わっているか。端どうしの接触は数えない。"""
    if abs(a1[0] - a2[0]) < 0.5 and abs(b1[1] - b2[1]) < 0.5:
        vertical, horizontal = (a1, a2), (b1, b2)
    elif abs(a1[1] - a2[1]) < 0.5 and abs(b1[0] - b2[0]) < 0.5:
        vertical, horizontal = (b1, b2), (a1, a2)
    else:
        return False
    x = vertical[0][0]
    y = horizontal[0][1]
    lo_x, hi_x = sorted((horizontal[0][0], horizontal[1][0]))
    lo_y, hi_y = sorted((vertical[0][1], vertical[1][1]))
    return lo_x + 0.5 < x < hi_x - 0.5 and lo_y + 0.5 < y < hi_y - 0.5


def _route_cross_count(
    route: list[tuple[float, float]], drawn: list[list[tuple[float, float]]]
) -> int:
    """先に引いた線と直角に交わる回数。

    交差した線は、どちらがどこへ行っているのか目で追えなくなる。
    箱を避けるだけでは足りないので、線どうしの交差も経路の選び方に入れる。
    """
    if not drawn:
        return 0
    return sum(
        1
        for a, b in zip(route, route[1:], strict=False)
        for other in drawn
        for c, d in zip(other, other[1:], strict=False)
        if _crosses_at_right_angle(a, b, c, d)
    )


def _ensure_outward(
    route: list[tuple[float, float]], face: str, gap: float = 16.0
) -> list[tuple[float, float]]:
    """面から外へ出る一段を足す。辺に沿って先に動く経路を斜めにしない。"""
    if len(route) < 2 or _exits_outward(route, face):
        return route
    x0, y0 = route[0]
    x1, y1 = route[1]
    if face == "left":
        mid = (x0 - gap, y0)
    elif face == "right":
        mid = (x0 + gap, y0)
    elif face == "top":
        mid = (x0, y0 - gap)
    elif face == "bottom":
        mid = (x0, y0 + gap)
    else:
        return route
    if face in ("left", "right"):
        corner = (mid[0], y1)
        if abs(x1 - x0) <= 0.5:
            # 辺に沿って縦へ動いていた → 外へ出て同じ高さへ
            rest = route[2:] if len(route) > 2 else []
            points = [route[0], mid, corner, *rest]
        else:
            points = [route[0], mid, corner, *route[1:]]
    else:
        corner = (x1, mid[1])
        if abs(y1 - y0) <= 0.5:
            rest = route[2:] if len(route) > 2 else []
            points = [route[0], mid, corner, *rest]
        else:
            points = [route[0], mid, corner, *route[1:]]
    out = [points[0]]
    for point in points[1:]:
        if abs(point[0] - out[-1][0]) > 0.5 or abs(point[1] - out[-1][1]) > 0.5:
            out.append(point)
    return out if len(out) > 1 else route


def _collapse_collinear(route: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """一直線上の中間点を落とす。迂回の折れが余分に見えるのを防ぐ。"""
    if len(route) < 3:
        return route
    out = [route[0]]
    for index in range(1, len(route) - 1):
        ax, ay = out[-1]
        bx, by = route[index]
        cx, cy = route[index + 1]
        # 同じ点は飛ばす
        if abs(bx - ax) <= 0.5 and abs(by - ay) <= 0.5:
            continue
        colinear = (abs(ax - bx) <= 0.5 and abs(bx - cx) <= 0.5) or (
            abs(ay - by) <= 0.5 and abs(by - cy) <= 0.5
        )
        if colinear:
            continue
        out.append(route[index])
    last = route[-1]
    if abs(last[0] - out[-1][0]) > 0.5 or abs(last[1] - out[-1][1]) > 0.5:
        out.append(last)
    return out if len(out) > 1 else route


def _detour_keep_faces(
    start: tuple[float, float],
    end: tuple[float, float],
    src_side: str,
    dst_side: str,
    boxes: list[tuple[float, float, float, float]],
    side: str,
) -> list[tuple[float, float]] | None:
    """同じ口のまま上下のすき間へ迂回する。口を付け替えると画面ハンドルと食い違う。"""
    if src_side not in ("left", "right") or dst_side not in ("left", "right"):
        return None
    pad = 24.0
    sx, sy = start
    tx, ty = end
    out = 16.0
    ox = sx + (out if src_side == "right" else -out if src_side == "left" else 0.0)
    ix = tx + (out if dst_side == "right" else -out if dst_side == "left" else 0.0)
    # 通り道の横幅に重なり、直進帯（同じ高さ付近）を塞ぐ箱だけ避ける。
    # 下の段まで全部拾うと、無関係な拠点の下を大回りする。
    lo_x, hi_x = (min(ox, ix), max(ox, ix))
    band_lo, band_hi = (min(sy, ty) - pad, max(sy, ty) + pad)
    channel = max(sy, ty) + pad if side == "below" else min(sy, ty) - pad
    for x1, y1, x2, y2 in boxes:
        if x2 < lo_x - 1 or x1 > hi_x + 1:
            continue
        if y2 < band_lo or y1 > band_hi:
            continue
        if side == "below":
            channel = max(channel, y2 + pad)
        else:
            channel = min(channel, y1 - pad)
    return [
        (sx, sy),
        (ox, sy),
        (ox, channel),
        (ix, channel),
        (ix, ty),
        (tx, ty),
    ]


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
    avoid: list[list[tuple[float, float]]] | None = None,
) -> list[tuple[float, float]]:
    if src_t is None:
        src_t = 0.5 if count <= 1 else 0.25 + 0.5 * (rank / max(count - 1, 1))
    if dst_t is None:
        dst_t = _face_t(dst_box, dst_side, src_box)
    start = _anchor(src_box, src_side, src_t)
    end = _anchor(dst_box, dst_side, dst_t)
    candidates: list[tuple[str, str, list[tuple[float, float]]]] = []
    pierce_boxes = [
        _xywh_bounds(src_box),
        _xywh_bounds(dst_box),
        *((rect.x1, rect.y1, rect.x2, rect.y2) for rect in obstacles),
    ]

    def consider(face_s: str, face_t: str, route: list[tuple[float, float]] | None) -> None:
        if not route or not _is_orthogonal(route):
            return
        fixed = _collapse_collinear(_ensure_outward(route, face_s))
        if _is_orthogonal(fixed):
            candidates.append((face_s, face_t, fixed))

    drawn = avoid or []

    bus = _orthogonal_bus(start, src_side, end, dst_side, rank, count, pierce_boxes)
    if bus:
        bus = _collapse_collinear(_ensure_outward(bus, src_side))
        if (
            _route_pierce_count(bus, pierce_boxes) == 0
            and _exits_outward(bus, src_side)
            and _arrives_from_outside(bus, dst_side)
            and _route_cross_count(bus, drawn) == 0
        ):
            return bus
    consider(src_side, dst_side, bus)
    consider(src_side, dst_side, _manhattan(start, end, src_side, dst_side))
    consider(src_side, dst_side, _route(start, end, obstacles))
    # 口は描画前に固定。左右のまま上下へ迂回だけ試す（口の付け替えはしない）
    if src_side in ("left", "right") and dst_side in ("left", "right"):
        for side in ("below", "above"):
            consider(
                src_side,
                dst_side,
                _detour_keep_faces(start, end, src_side, dst_side, pierce_boxes, side),
            )

    def score(item: tuple[str, str, list[tuple[float, float]]]) -> tuple:
        face_s, face_t, route = item
        bends = max(0, len(route) - 2)
        return (
            _route_pierce_count(route, pierce_boxes),
            0 if _exits_outward(route, face_s) else 1,
            0 if _arrives_from_outside(route, face_t) else 1,
            # 折れは Visio の定石どおり2つまで。ここを崩してまで交差は避けない
            0 if bends <= MAX_BENDS else 1,
            # そのうえで、先に引いた線と交わらない経路を採る。
            # 交差した線は、どちらがどこへ行っているのか目で追えなくなる
            _route_cross_count(route, drawn),
            bends,
            _length(route),
        )

    if not candidates:
        return [start, end]
    return min(candidates, key=score)[2]


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
    wires: list[list[tuple[float, float]]], *, radius: float = 5.0, end_keep: float = 24.0
) -> list[list[list[tuple[float, float]]]]:
    """後から描く線が先の線と直角に交わるとき、弧ではなく隙間を空ける。

    機器の口のすぐ横で切ると、切れ端が口に張り付いて線が歪んで見える。
    端点から end_keep 以内の交点は切らない。
    """
    out: list[list[list[tuple[float, float]]]] = []
    earlier: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def near(p: tuple[float, float], q: tuple[float, float]) -> bool:
        return abs(p[0] - q[0]) <= 0.5 and abs(p[1] - q[1]) <= 0.5

    def away_from_ends(cross: tuple[float, float], wire: list[tuple[float, float]]) -> bool:
        start, end = wire[0], wire[-1]
        for tip in (start, end):
            if abs(cross[0] - tip[0]) + abs(cross[1] - tip[1]) < end_keep:
                return False
        return True

    for wire in wires:
        pieces: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = [wire[0]]
        for a, b in zip(wire, wire[1:], strict=False):
            holes = [
                cross
                for seg in earlier
                if (cross := _interior_cross(a, b, seg[0], seg[1])) is not None
                and away_from_ends(cross, wire)
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


def _card_lines(node) -> list[str]:
    """箱の本文。画面と同じ並びで、空の行は出さない。

    種別と拠点は上段と枠で示すので、ここには入れない。現場で要るのは
    型番・銘板（シリアル）・管理IP の3つ。
    """
    lines: list[str] = []
    product = f"{node.vendor} {node.model}".strip()
    if product:
        lines.append(product)
    if node.serial_number:
        lines.append(f"S/N {node.serial_number}")
    if node.mgmt_ip:
        lines.append(node.mgmt_ip)
    return lines


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
    if chosen is None:
        # どの候補も空いていなかった。線の外向きへ少しずつ逃がして、
        # 空くところまで押し出す。丸い角の近くから出た線で起きやすい
        for step in range(1, 9):
            moved_x = px + ux * step * 6.0
            moved_y = py + uy * step * 6.0
            candidate = svglayout.text_rect(moved_x, moved_y - 3, text, _LABEL_FONT, anchor, angle)
            if not placement.collides(candidate):
                px, py, rect = moved_x, moved_y, candidate
                break
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
