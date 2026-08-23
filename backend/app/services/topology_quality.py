"""構成図が読めるかの判定。好みの前に、ここを通らない図は出さない。

良い図: 印刷してラックの前で指差せる。誰が上流か、どこが冗長か、どのポートか。
悪い図: 放射状、斜め線、冗長の縦積み、対向の2台目を折る、箱を貫く線。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.services.topology import (
    _DETAIL_HEIGHT,
    _OUTSIDE_HEIGHT,
    MIN_BOX_GAP,
    TopologyGraph,
    _busiest_sides,
    _visual_bottom,
    _visual_left,
    _visual_right,
    _visual_top,
)


@dataclass(frozen=True)
class Finding:
    ok: bool
    code: str
    detail: str


def evaluate_site_diagram(graph: TopologyGraph) -> list[Finding]:
    """拠点で絞った物理図が、資料として読めるか。"""
    findings: list[Finding] = []
    if len(graph.nodes) < 2:
        return [Finding(False, "too_small", "機器が少なすぎて図にならない")]

    inside = [n for n in graph.nodes if not n.outside]
    outside = [n for n in graph.nodes if n.outside]
    if not inside:
        return [Finding(False, "no_inside", "自拠点の機器が無い")]

    findings.append(Finding(True, "has_spine", f"自拠点 {len(inside)} 台"))

    busiest_tb, busiest_lr = _busiest_sides(graph.nodes, graph.edges)
    overlap = False
    for i, a in enumerate(graph.nodes):
        for b in graph.nodes[i + 1 :]:
            a_tb = busiest_tb.get(a.device_id, 0)
            b_tb = busiest_tb.get(b.device_id, 0)
            a_lr = busiest_lr.get(a.device_id, 0)
            b_lr = busiest_lr.get(b.device_id, 0)
            # 見た目の箱で判定。保存座標の差だけだと広げた箱の食い込みを見逃す。
            # 図の外の相手は名前だけの小さい箱なので、その高さで見る
            a_base = _OUTSIDE_HEIGHT if a.outside else _DETAIL_HEIGHT
            b_base = _OUTSIDE_HEIGHT if b.outside else _DETAIL_HEIGHT
            if (
                _visual_left(a.x, a_tb) < _visual_right(b.x, b_tb) - 1
                and _visual_left(b.x, b_tb) < _visual_right(a.x, a_tb) - 1
                and _visual_top(a.y, a_lr, base=a_base) < _visual_bottom(b.y, b_lr, base=b_base) - 1
                and _visual_top(b.y, b_lr, base=b_base) < _visual_bottom(a.y, a_lr, base=a_base) - 1
            ):
                overlap = True
                findings.append(
                    Finding(False, "overlap", f"{a.hostname} と {b.hostname} が重なっている")
                )
    if not overlap:
        findings.append(Finding(True, "no_overlap", "箱が重なっていない"))

    folded = [n.hostname for n in graph.nodes if n.hidden_peers]
    if folded:
        findings.append(
            Finding(False, "folded_peer", f"対向を折りたたんでいる: {', '.join(folded)}")
        )
    else:
        findings.append(Finding(True, "no_fold", "境界の対向を折りたたんでいない"))

    by_site: dict[object, list] = defaultdict(list)
    for node in graph.nodes:
        key = node.site_id if node.site_id is not None else node.device_id
        by_site[key].append(node)
    stacked = False
    for members in by_site.values():
        if len(members) < 2:
            continue
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if abs(left.y - right.y) <= 80 or abs(left.x - right.x) >= 80:
                    continue
                if not _should_sit_abreast(left, right, graph):
                    continue
                stacked = True
                findings.append(
                    Finding(
                        False,
                        "redundant_stacked",
                        f"{left.hostname} と {right.hostname} が縦に積まれている",
                    )
                )
    if not stacked:
        findings.append(Finding(True, "redundant_side", "同じ拠点の複数台は横並び"))

    up_peers = [n for n in outside if (n.peer_kind or "") == "up"]
    down_peers = [n for n in outside if n not in up_peers]
    if up_peers:
        inside_top = min(i.y for i in inside)
        inside_left = min(i.x for i in inside)
        above = all(n.y < inside_top - 40 for n in up_peers)
        left = all(n.x < inside_left - 40 for n in up_peers)
        if above:
            findings.append(Finding(True, "uplink_above", "上位が自拠点の上にある"))
        elif left:
            # 左右配置（orientation=horizontal）では上位を左に置く
            findings.append(Finding(True, "uplink_left", "上位が自拠点の左にある"))
        else:
            findings.append(Finding(False, "uplink_not_above", "上位が上（または左）に無い"))
        if len(up_peers) >= 2:
            ys = [n.y for n in up_peers]
            xs = [n.x for n in up_peers]
            if max(ys) - min(ys) < 40 or max(xs) - min(xs) < 40:
                findings.append(Finding(True, "uplink_row", "上位の冗長が同じ段にある"))
            else:
                findings.append(Finding(False, "uplink_not_row", "上位の冗長が同じ段に無い"))

    if down_peers:
        # 「右の列にある」は、同じ段の機器より右にある、という意味。
        # 図全体の右端と比べると、離れた段にいる機器に引きずられ、対向を
        # 隣へ寄せられなくなる（寄せた方が、どの機器の先かを読みやすい）
        def _clear_of_its_row(peer) -> bool:
            same_row = [n for n in inside if abs(n.y - peer.y) < 160]
            edge = max((n.x for n in same_row), default=None)
            if edge is None:
                # 同じ段に内側の機器がいないなら、混ざりようがない
                return True
            return peer.x > edge + 20

        if all(_clear_of_its_row(n) for n in down_peers):
            findings.append(Finding(True, "peers_right", "配下拠点が右の列にある"))
        else:
            findings.append(Finding(False, "peers_not_right", "配下拠点が右の列に無い"))
        rows: dict[object, list] = defaultdict(list)
        for node in down_peers:
            rows[node.site_id if node.site_id is not None else node.device_id].append(node)
        lefts = [min(n.x for n in members) for members in rows.values()]
        if max(lefts) - min(lefts) < 80:
            findings.append(Finding(True, "peer_column", "配下拠点の列が揃っている"))
        else:
            findings.append(Finding(False, "peer_not_column", "配下拠点が列になっていない"))

    return findings


def evaluate_svg(svg: str, graph: TopologyGraph) -> list[Finding]:
    """書き出した図が、資料の線の定石を守っているか。色や地色の好みは見ない。"""
    findings: list[Finding] = []

    if _has_diagonal(svg):
        findings.append(Finding(False, "diagonal", "線が斜め。縦横だけで通すこと"))
    else:
        findings.append(Finding(True, "orthogonal", "線は縦横だけ"))

    boxes = _svg_boxes(svg)
    wires = _svg_polylines(svg)

    # 描いた箱そのもので見る。座標の上では離れていても、書き出しが別の
    # 大きさで描けば重なる。実際にそうなっていた（対向の箱だけ簡易表示で
    # 大きく描かれ、段の間隔より高かった）ので、出来上がりの側から見る
    touching = _touching_boxes(boxes)
    if touching:
        near, gap = touching
        findings.append(
            Finding(False, "boxes_touch", f"箱どうしが近すぎる（すき間 {gap:.0f}px）: {near}")
        )
    else:
        findings.append(Finding(True, "boxes_apart", "箱どうしが離れている"))
    pierced = False
    for wire in wires:
        for a, b in zip(wire, wire[1:], strict=False):
            for box in boxes:
                if _segment_pierces(box, a, b):
                    pierced = True
                    findings.append(Finding(False, "pierce", "線が箱を貫いている"))
                    break
            if pierced:
                break
        if pierced:
            break
    if not pierced:
        findings.append(Finding(True, "no_pierce", "線が箱を貫いていない"))

    # 1本あたり折れが多すぎると Visio の定石から外れる（同じ段の迂回は2折れ）
    too_bent = any(max(0, len(wire) - 2) > 2 for wire in wires)
    if too_bent:
        findings.append(Finding(False, "too_bent", "線の折れが多すぎる"))
    else:
        findings.append(Finding(True, "bend_ok", "線の折れは許容内"))

    # 直角交差は隙間切りで切れ端になりやすい。通せるなら交差させない。
    segs = [(a, b) for wire in wires for a, b in zip(wire, wire[1:], strict=False)]
    crossed = False
    for i, (a1, a2) in enumerate(segs):
        for b1, b2 in segs[i + 1 :]:
            if _score_cross(a1, a2, b1, b2):
                crossed = True
                findings.append(Finding(False, "wire_cross", "線どうしが直角に交差している"))
                break
        if crossed:
            break
    if not crossed:
        findings.append(Finding(True, "no_wire_cross", "線どうしが交差していない"))

    for node in graph.nodes:
        if node.hostname not in svg:
            findings.append(Finding(False, "missing_node", f"{node.hostname} が図に無い"))
            break
    else:
        findings.append(Finding(True, "all_nodes", "画面の機器が図に残っている"))

    return findings


#: 箱どうしに最低限空けるすき間。配置側（topology.MIN_BOX_GAP）と同じ値を使う。
#: 別々に持つと、置く側と見る側で合格の線がずれる
_MIN_BOX_GAP = MIN_BOX_GAP


def _touching_boxes(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[str, float] | None:
    """描いた箱で、いちばん近い組が近すぎないか。

    戻り値は (その組の位置, すき間)。すき間が負なら重なっている。
    """
    worst: tuple[str, float] | None = None
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            # 箱の中に描いた箱（詳細表示の行）は、入れ子なので見ない
            if _contains(a, b) or _contains(b, a):
                continue
            ax, ay, ax2, ay2 = a
            bx, by, bx2, by2 = b
            gap = max(ax - bx2, bx - ax2, ay - by2, by - ay2)
            if gap >= _MIN_BOX_GAP:
                continue
            if worst is None or gap < worst[1]:
                worst = (f"({ax:.0f},{ay:.0f}) と ({bx:.0f},{by:.0f})", gap)
    return worst


def _contains(
    outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]
) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def is_acceptable(findings: list[Finding]) -> bool:
    return bool(findings) and all(item.ok for item in findings)


def _should_sit_abreast(left, right, graph: TopologyGraph) -> bool:
    """冗長の対だけ横並びが要る。縦の背骨や、上位と配下のすれ違いは対象外。"""
    if left.device_type != right.device_type:
        return False
    neighbours: dict[int, set[int]] = defaultdict(set)
    bundle = 0
    for edge in graph.edges:
        pair = {edge.source, edge.target}
        if pair == {left.device_id, right.device_id}:
            bundle += 1
            continue
        neighbours[edge.source].add(edge.target)
        neighbours[edge.target].add(edge.source)
    common = neighbours[left.device_id] & neighbours[right.device_id]
    if len(common) >= 2:
        return True
    return bundle >= 2 and left.site_id == right.site_id


@dataclass(frozen=True)
class Score:
    total: float
    parts: dict[str, float]


def score_diagram(graph: TopologyGraph, svg: str | None = None) -> Score:
    """資料としての点数。合格は100を超えること。超えてからも上げられる。

    最適化は、正解の段と列を崩さず、中心・間隔・交差・余白を詰めること。
    初期配置へ組み直すことではない。
    """
    parts: dict[str, float] = {}
    findings = evaluate_site_diagram(graph)
    if svg:
        findings = findings + evaluate_svg(svg, graph)
    failed = sum(1 for item in findings if not item.ok)
    passed = sum(1 for item in findings if item.ok)
    parts["readable"] = max(0.0, passed * 8 - failed * 20)

    inside = [n for n in graph.nodes if not n.outside]
    outside = [n for n in graph.nodes if n.outside]
    above = [n for n in outside if (n.peer_kind or "") == "up"]
    if inside and above:
        hub = min(inside, key=lambda n: (n.y, n.x))
        hub_c = hub.x + 95
        above_c = sum(n.x + 95 for n in above) / len(above)
        err = abs(hub_c - above_c)
        parts["center"] = max(0.0, 20.0 * (1.0 - err / 120.0))
    else:
        parts["center"] = 8.0

    parts["even"] = _even_score(graph)
    parts["tight"] = _tight_score(graph)
    parts["corridor"] = _corridor_score(graph)
    if svg:
        parts["wires"] = _wire_score(svg)
        parts["labels"] = _label_score(svg)
    else:
        parts["wires"] = 5.0
        parts["labels"] = 4.0

    total = round(sum(parts.values()), 1)
    return Score(total=total, parts=parts)


def _even_score(graph: TopologyGraph) -> float:
    rows: dict[tuple[str, int], list[float]] = defaultdict(list)
    cols: dict[tuple[str, int], list[float]] = defaultdict(list)
    for node in graph.nodes:
        side = "out" if node.outside else "in"
        rows[(side, round(node.y / 10))].append(node.x)
        cols[(side, round(node.x / 10))].append(node.y)
    penalties = 0.0
    counted = 0
    for values in list(rows.values()) + list(cols.values()):
        if len(values) < 3:
            continue
        ordered = sorted(values)
        gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)]
        mean = sum(gaps) / len(gaps)
        if mean <= 0:
            continue
        counted += 1
        var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
        penalties += min(1.0, (var**0.5) / mean)
    if not counted:
        return 12.0
    return max(0.0, 15.0 * (1.0 - penalties / counted))


def _corridor_score(graph: TopologyGraph) -> float:
    """自拠点と配下の列の間。通り道より広い空白は減点。"""
    inside = [n for n in graph.nodes if not n.outside]
    down = [n for n in graph.nodes if n.outside and (n.peer_kind or "") == "down"]
    if not inside or not down:
        return 8.0
    links: dict[int, int] = defaultdict(int)
    for edge in graph.edges:
        links[edge.source] += 1
        links[edge.target] += 1
    right = max(_visual_right(n.x, links[n.device_id]) for n in inside)
    left = min(n.x for n in down)
    gap = left - right
    if gap < 80:
        return 3.0
    if gap <= 180:
        return 12.0
    return max(0.0, 12.0 * (1.0 - (gap - 180.0) / 220.0))


def _label_score(svg: str) -> float:
    """ポート名どうしが近すぎないこと。箱の隙間に並べると読めない。"""
    labels: list[tuple[float, float]] = []
    for match in re.finditer(r'<text class="port-label" x="([-\d.]+)" y="([-\d.]+)"', svg):
        labels.append((float(match.group(1)), float(match.group(2))))
    if len(labels) < 2:
        return 8.0
    hits = 0
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            if abs(a[0] - b[0]) < 36 and abs(a[1] - b[1]) < 12:
                hits += 1
    if hits == 0:
        return 10.0
    if hits <= 2:
        return 6.0
    return 2.0


def _tight_score(graph: TopologyGraph) -> float:
    if len(graph.nodes) < 2:
        return 5.0
    width = max(n.x for n in graph.nodes) - min(n.x for n in graph.nodes) + 190
    height = max(n.y for n in graph.nodes) - min(n.y for n in graph.nodes) + 78
    area = max(width * height, 1.0)
    boxes = len(graph.nodes) * 190 * 78
    ratio = boxes / area
    return max(0.0, min(12.0, 12.0 * ratio / 0.22))


def _wire_score(svg: str) -> float:
    wires = _svg_polylines(svg)
    if _has_diagonal(svg):
        return 0.0
    crosses = 0
    segs = [(a, b) for wire in wires for a, b in zip(wire, wire[1:], strict=False)]
    for i, (a1, a2) in enumerate(segs):
        for b1, b2 in segs[i + 1 :]:
            if _score_cross(a1, a2, b1, b2):
                crosses += 1
    if crosses == 0:
        return 18.0
    if crosses <= 2:
        return 12.0
    if crosses <= 5:
        return 6.0
    return 2.0


def _score_cross(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    ah = abs(a1[1] - a2[1]) <= 0.5
    av = abs(a1[0] - a2[0]) <= 0.5
    bh = abs(b1[1] - b2[1]) <= 0.5
    bv = abs(b1[0] - b2[0]) <= 0.5
    if ah and bv:
        x, y = b1[0], a1[1]
        return (
            min(a1[0], a2[0]) + 3 < x < max(a1[0], a2[0]) - 3
            and min(b1[1], b2[1]) + 3 < y < max(b1[1], b2[1]) - 3
        )
    if av and bh:
        x, y = a1[0], b1[1]
        return (
            min(b1[0], b2[0]) + 3 < x < max(b1[0], b2[0]) - 3
            and min(a1[1], a2[1]) + 3 < y < max(a1[1], a2[1]) - 3
        )
    return False


def _has_diagonal(svg: str) -> bool:
    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split()]
        for a, b in zip(pts, pts[1:], strict=False):
            if abs(a[0] - b[0]) > 8 and abs(a[1] - b[1]) > 8:
                return True
    return False


def _svg_polylines(svg: str) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        out.append(
            [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split()]
        )
    return out


def _svg_boxes(svg: str) -> list[tuple[float, float, float, float]]:
    """機器の箱。ラック・部屋の枠（class="group-frame"）は含めない。

    枠は機器を囲むものなので、そこへ線が入るのは当たり前で、貫きではない。
    枠まで数えると、枠を1つ置いただけで図が「線が箱を貫いている」になる。
    """
    boxes: list[tuple[float, float, float, float]] = []
    for match in re.finditer(
        r'<rect (?:class="([^"]*)" )?x="([\d.]+)" y="([\d.]+)" '
        r'width="([\d.]+)" height="([\d.]+)"',
        svg,
    ):
        if (match.group(1) or "") == "group-frame":
            continue
        x, y, w, h = (float(match.group(i)) for i in range(2, 6))
        if w > 80 and h > 30:
            boxes.append((x, y, x + w, y + h))
    return boxes


def _segment_pierces(
    box: tuple[float, float, float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """端が箱の辺にある接続は貫きではない。箱の中を通過する線だけを落とす。

    長い線分の固定サンプルだと、箱の端をかすめる貫きを見逃すことがある。
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
    return t1 - t0 > 1e-4 and t0 < 1.0 - 1e-9 and t1 > 1e-9
