"""構成図が読めるかの判定。好みの前に、ここを通らない図は出さない。

良い図: 印刷してラックの前で指差せる。誰が上流か、どこが冗長か、どのポートか。
悪い図: 放射状、斜め線、冗長の縦積み、対向の2台目を折る、箱を貫く線。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.services.topology import TopologyGraph


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

    overlap = False
    for i, a in enumerate(graph.nodes):
        for b in graph.nodes[i + 1 :]:
            if abs(a.x - b.x) < 160 and abs(a.y - b.y) < 60:
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
        ys = sorted(n.y for n in members)
        xs = sorted(n.x for n in members)
        # 同じ拠点の2台が縦に積まれ、横に開いていない
        if ys[-1] - ys[0] > 80 and xs[-1] - xs[0] < 80:
            # 自拠点のルータとスイッチは縦の背骨。冗長対の縦積みだけを落とす
            if any(n.outside for n in members) or len({n.device_type for n in members}) == 1:
                stacked = True
                findings.append(
                    Finding(
                        False,
                        "redundant_stacked",
                        f"{members[0].hostname} と {members[-1].hostname} が縦に積まれている",
                    )
                )
    if not stacked:
        findings.append(Finding(True, "redundant_side", "同じ拠点の複数台は横並び"))

    up_peers = [n for n in outside if (n.peer_kind or "") == "up"]
    down_peers = [n for n in outside if n not in up_peers]
    if up_peers:
        if all(n.y < min(i.y for i in inside) - 40 for n in up_peers):
            findings.append(Finding(True, "uplink_above", "上位が自拠点の上にある"))
        else:
            findings.append(Finding(False, "uplink_not_above", "上位が上に無い"))
        if len(up_peers) >= 2:
            ys = [n.y for n in up_peers]
            if max(ys) - min(ys) < 40:
                findings.append(Finding(True, "uplink_row", "上位の冗長が同じ高さ"))
            else:
                findings.append(Finding(False, "uplink_not_row", "上位の冗長が同じ高さに無い"))

    if down_peers:
        right = max(n.x for n in inside)
        if all(n.x > right + 80 for n in down_peers):
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
    """書き出した図が、資料の線の定石を守っているか。"""
    findings: list[Finding] = []
    if "#f4f1ea" in svg or "#fbfaf7" in svg:
        findings.append(Finding(True, "paper", "紙の地色で出している"))
    else:
        findings.append(Finding(False, "not_paper", "画面の白キャンバスのまま出している"))

    if _has_diagonal(svg):
        findings.append(Finding(False, "diagonal", "線が斜め。縦横だけで通すこと"))
    else:
        findings.append(Finding(True, "orthogonal", "線は縦横だけ"))

    boxes = _svg_boxes(svg)
    wires = _svg_polylines(svg)
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

    for node in graph.nodes:
        if node.hostname not in svg:
            findings.append(Finding(False, "missing_node", f"{node.hostname} が図に無い"))
            break
    else:
        findings.append(Finding(True, "all_nodes", "画面の機器が図に残っている"))

    return findings


def is_acceptable(findings: list[Finding]) -> bool:
    return bool(findings) and all(item.ok for item in findings)


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
    boxes: list[tuple[float, float, float, float]] = []
    for match in re.finditer(
        r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
    ):
        x, y, w, h = (float(match.group(i)) for i in range(1, 5))
        if w > 80 and h > 30:
            boxes.append((x, y, x + w, y + h))
    return boxes


def _segment_pierces(
    box: tuple[float, float, float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """端が箱の辺にある接続は貫きではない。箱の中を通過する線だけを落とす。"""
    x1, y1, x2, y2 = box
    pad = 4.0
    inner = (x1 + pad, y1 + pad, x2 - pad, y2 - pad)
    samples = 8
    hits = 0
    for i in range(1, samples):
        t = i / samples
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        if inner[0] < x < inner[2] and inner[1] < y < inner[3]:
            hits += 1
    return hits >= 3
