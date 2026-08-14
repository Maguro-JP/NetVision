"""トポロジ図をファイルに書き出す。

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

from dataclasses import dataclass
from math import atan2, cos, degrees, sin
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.services import localtime, portlabel, svglayout
from app.services.lifecycle import Severity
from app.services.portlabel import MEDIA_COLOR, MEDIA_LABEL, Media
from app.services.topology import TopologyGraph, get_graph

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
    """トポロジを SVG にする。位置は画面で整えたものをそのまま使う。

    `vlan` / `vrf` / `site_id` を指定すると、それに載っている機器だけを描く。
    セグメント単位・VRF 単位・拠点単位で資料を作るときに使う。

    `style="simple"` は機器名と結線だけの図。型番・IP・保守状態・VLAN を
    一切載せない。作業手順書や現場での指差し確認では、余計な情報が入っている方が
    読み違いのもとになるため、用途で図を分けられるようにしてある。

    絞り込んだ場合、外に出た対向機器は `show_peers` で簡易表示として残す。
    線ごと消すと、拠点の図で上位への接続が「無い」ように見えてしまうため。
    """
    simple = style == "simple"
    everything = graph
    nodes = graph.nodes
    if vlan is not None:
        nodes = [n for n in nodes if vlan in n.vlans]
    if vrf is not None:
        nodes = [n for n in nodes if vrf in n.vrfs]
    if site_id is not None:
        nodes = [n for n in nodes if n.site_id == site_id]

    peers: list[_Peer] = []
    if nodes is not graph.nodes:
        keep = {n.device_id for n in nodes}
        # 絞り込みの外に出た相手を、簡易表示で残す。
        # 線ごと消すと、上位への接続が「無い」ように見えてしまう
        peers = _boundary_peers(everything, keep) if show_peers else []
        graph = TopologyGraph(
            layer=graph.layer,
            nodes=nodes,
            edges=[e for e in graph.edges if e.source in keep and e.target in keep],
        )
    if not nodes:
        return _empty_svg(title, layer=graph.layer)

    min_x = min([n.x for n in nodes] + [p.x for p in peers])
    min_y = min([n.y for n in nodes] + [p.y for p in peers])
    max_x = max([n.x + NODE_W for n in nodes] + [p.x + PEER_W for p in peers])
    max_y = max([n.y + NODE_H for n in nodes] + [p.y + PEER_H for p in peers])

    # 図の左上を原点側へ寄せ、余白と凡例・見出しの分を確保する
    legend_h = 0.0 if simple else LEGEND_H
    dx = MARGIN - min_x
    dy = MARGIN + 34.0 - min_y
    width = max_x - min_x + MARGIN * 2
    height = max_y - min_y + MARGIN * 2 + 34.0 + legend_h

    at = {n.device_id: (n.x + dx, n.y + dy) for n in nodes}
    parts: list[str] = []
    # 凡例に出すのは実際に使われている種別だけ。使っていない色を並べても読み手が困る
    used_media: set[Media] = set()

    # 機器の箱と簡易ノードを先に「埋まっている場所」として登録しておく。
    # ラベルはこれを避けて置く。少し広げるのは、枠に文字が接すると読みにくいため
    placement = svglayout.Placement(
        [svglayout.Rect(px, py, px + NODE_W, py + NODE_H).grow(3) for px, py in at.values()]
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

    # 同じ機器同士を何本も結んでいる場合（LAG など）に、線を平行にずらすための番号。
    # 中心同士を直線で結ぶだけだと完全に重なって1本に見える
    parallel = _parallel_index(graph)

    # 迂回の判定に使う箱。線の当事者になっていない機器だけを障害物として扱う
    boxes = {
        n.device_id: svglayout.Rect(
            at[n.device_id][0],
            at[n.device_id][1],
            at[n.device_id][0] + NODE_W,
            at[n.device_id][1] + NODE_H,
        )
        for n in nodes
    }
    peer_boxes = [
        svglayout.Rect(p.x + dx, p.y + dy, p.x + dx + PEER_W, p.y + dy + PEER_H) for p in peers
    ]

    # --- 結線（ノードより先に描いて背面に回す） ---
    for edge in graph.edges:
        source, target = at.get(edge.source), at.get(edge.target)
        if source is None or target is None:
            continue
        x1, y1 = source[0] + NODE_W / 2, source[1] + NODE_H / 2
        x2, y2 = target[0] + NODE_W / 2, target[1] + NODE_H / 2

        shift = parallel.get(edge.id, 0.0)
        if shift:
            length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
            offset_x = -(y2 - y1) / length * shift
            offset_y = (x2 - x1) / length * shift
            x1, y1 = x1 + offset_x, y1 + offset_y
            x2, y2 = x2 + offset_x, y2 + offset_y

        # 両側から確認できた結線は実線、片側だけのものは破線にする。
        # シンプル図では全部実線にする。破線は「取り込みの確からしさ」の話で、
        # 現場で見る図に混ぜると「未接続」と読み違えられるため
        dash = "" if (simple or edge.confirmed_both_ways) else ' stroke-dasharray="6 4"'
        stroke = "#4a5364" if simple else "#8a94a6"
        obstacles = [
            rect.grow(2.0)
            for device_id, rect in boxes.items()
            if device_id not in (edge.source, edge.target)
        ] + [rect.grow(2.0) for rect in peer_boxes]
        points = _route((x1, y1), (x2, y2), obstacles)
        parts.append(
            f'<polyline points="{_path_points(points)}" fill="none" '
            f'stroke="{stroke}" stroke-width="1.6"{dash}/>'
        )
        # ポート名の向きは、その端から出ていく最初の区間で決める
        nx1, ny1 = points[1]
        nx2, ny2 = points[-2]

        if edge.label:
            # 人が付けた名前は線の中央に置く。ポート名とは性質が違うため
            parts.append(_center_label(*_path_middle(points), edge.label, placement))
        else:
            # ポート名はそれぞれの機器側に置く。1本の線に2つ並べると
            # 「左が上位」のような規則を覚える必要が出て、図の向きが変われば
            # 逆に読んでしまう。ポートの隣にあれば規則は要らない
            parts.append(
                _port_label(
                    x1, y1, nx1, ny1, edge.source_interface, port_style, along_line, placement
                )
            )
            parts.append(
                _port_label(
                    x2, y2, nx2, ny2, edge.target_interface, port_style, along_line, placement
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
        ax, ay = anchor[0] + NODE_W / 2, anchor[1] + NODE_H / 2
        cx, cy = px + PEER_W / 2, py + PEER_H / 2

        obstacles = [
            rect.grow(2.0) for device_id, rect in boxes.items() if device_id != peer.anchor
        ] + [
            svglayout.Rect(q.x + dx, q.y + dy, q.x + dx + PEER_W, q.y + dy + PEER_H).grow(2.0)
            for q in peers
            if q is not peer
        ]
        points = _route((ax, ay), (cx, cy), obstacles)
        parts.append(
            f'<polyline points="{_path_points(points)}" fill="none" '
            f'stroke="#8a94a6" stroke-width="1.6"'
            + ("" if peer.confirmed else ' stroke-dasharray="6 4"')
            + "/>"
        )
        parts.append(_peer_box(px, py, peer, simple))
        # 図の中に残っている側のポート名は、通常どおり機器の隣に出す
        parts.append(
            _port_label(ax, ay, *points[1], peer.local_interface, port_style, along_line, placement)
        )
        if peer.local_interface:
            used_media.add(portlabel.media_of(peer.local_interface))

    # --- 機器 ---
    for node in nodes:
        x, y = at[node.device_id]
        if simple:
            # 名前だけ。枠は輪郭が分かる程度の細さに留める
            parts.append(
                f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{NODE_W:.0f}" '
                f'height="{NODE_H:.0f}" rx="7" fill="#ffffff" stroke="#4a5364" '
                f'stroke-width="1.6"/>'
                f'<text x="{x + NODE_W / 2:.0f}" y="{y + NODE_H / 2 + 5:.0f}" font-size="14" '
                f'font-weight="700" fill="#1a1a1a" text-anchor="middle">'
                f"{_esc(_clip(node.hostname, 22))}</text></g>"
            )
            continue

        color = SEVERITY_COLOR.get(str(node.severity), SEVERITY_COLOR[str(Severity.UNKNOWN)])
        parts.append(
            f'<g><rect x="{x:.0f}" y="{y:.0f}" width="{NODE_W:.0f}" height="{NODE_H:.0f}" '
            f'rx="7" fill="#f7f9fc" stroke="{color}" stroke-width="2"/>'
            # 状態が一目で分かるよう左端に色帯を置く（白黒印刷でも位置で区別できる）
            f'<rect x="{x:.0f}" y="{y:.0f}" width="5" height="{NODE_H:.0f}" '
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


def _route(
    p1: tuple[float, float],
    p2: tuple[float, float],
    obstacles: list[svglayout.Rect],
    depth: int = 0,
) -> list[tuple[float, float]]:
    """関係の無い機器の箱を避けて通る折れ線を返す。

    直線で結ぶと、間にある機器の上を通ってしまうことがある。図の上では
    「通過」と「接続」を見分けられないので、その機器にも繋がっていると
    読まれてしまう。専用線が FW を貫いて見えるのがこれ。

    迂回は上下左右の4方向を試し、他の箱にも当たらず、遠回りの少ないものを採る。
    どうしても避けられない場合は直線のまま出す。線を消す方が誤解を招くため。
    """
    blockers = [rect for rect in obstacles if svglayout.segment_hits(rect, p1, p2)]
    if not blockers:
        return [p1, p2]
    if depth >= 3:
        return [p1, p2]

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
    # 辺の外側と、角の外側。斜めの線は角を回る方が短く済む
    candidates = [
        (mid_x, top),
        (mid_x, bottom),
        (left, mid_y),
        (right, mid_y),
        (left, top),
        (right, top),
        (left, bottom),
        (right, bottom),
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

    points = best or [p1, p2]

    # 同じ点が続くと、そこだけ向きが決まらない。折れ線から落とす
    out = [points[0]]
    for point in points[1:]:
        if abs(point[0] - out[-1][0]) > 0.5 or abs(point[1] - out[-1][1]) > 0.5:
            out.append(point)
    return out if len(out) > 1 else [p1, p2]


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
    base = _exit_distance(ux, uy) + _LABEL_GAP
    limit = length * 0.45
    spots = [
        (
            x1 + ux * (base + step) - uy * offset * side,
            y1 + uy * (base + step) + ux * offset * side,
        )
        for step, offset, side in _candidate_offsets(base, limit)
    ]

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


def _exit_distance(ux: float, uy: float) -> float:
    """ノードの中心から、その向きで枠を出るまでの距離。"""
    half_w, half_h = NODE_W / 2, NODE_H / 2
    tx = half_w / abs(ux) if abs(ux) > 1e-6 else float("inf")
    ty = half_h / abs(uy) if abs(uy) > 1e-6 else float("inf")
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
        get_graph(db, layer, orientation=orientation),
        title=title,
        vlan=vlan,
        vrf=vrf,
        site_id=site_id,
        style=style,
        port_style=port_style,
        along_line=along_line,
        show_peers=show_peers,
    )
