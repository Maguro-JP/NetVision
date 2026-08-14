"""構成図が読めるかの判定。好みの前に、ここを通らない図は出さない。

良い図: 段が揃い、上位が上、配下拠点は右の列、箱が重ならない、線は箱を貫かない。
悪い図: 放射状、フルメッシュの斜め線、冗長が縦積み、営業所が自拠点の真下。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.topology import TopologyGraph


@dataclass(frozen=True)
class Finding:
    ok: bool
    code: str
    detail: str


def evaluate_site_diagram(graph: TopologyGraph) -> list[Finding]:
    """拠点で絞った物理図が、見本と同じ読み方になるか。"""
    findings: list[Finding] = []
    if len(graph.nodes) < 2:
        return [Finding(False, "too_small", "機器が少なすぎて図にならない")]

    inside = [n for n in graph.nodes if not n.outside]
    outside = [n for n in graph.nodes if n.outside]
    if not inside:
        return [Finding(False, "no_inside", "自拠点の機器が無い")]

    findings.append(Finding(True, "has_spine", f"自拠点 {len(inside)} 台"))

    # 箱の重なり
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

    # 自拠点は縦の背骨（2台以上なら x が近い）
    if len(inside) >= 2:
        xs = [n.x for n in inside]
        ys = [n.y for n in inside]
        if max(xs) - min(xs) < 80:
            findings.append(Finding(True, "spine_column", "自拠点が縦に揃っている"))
        elif max(ys) - min(ys) < 80:
            findings.append(Finding(True, "spine_row", "自拠点が横に揃っている（左右配置）"))
        else:
            findings.append(Finding(False, "spine_scatter", "自拠点が斜めに散っている"))

    # 上位は上。見ている支社が中心になっても、センターを配下に落とさない
    above = [n for n in outside if n.y < min(i.y for i in inside) - 40]
    if above:
        findings.append(Finding(True, "uplink_above", "上位が自拠点の上にある"))
    elif outside:
        findings.append(Finding(False, "uplink_not_above", "上位が上に無い"))

    above_ids = {n.device_id for n in above}
    below = [n for n in outside if n.device_id not in above_ids]
    if len(below) >= 2:
        right = max(n.x for n in inside)
        if all(n.x > right + 80 for n in below):
            findings.append(Finding(True, "peers_right", "配下拠点が右の列にある"))
        else:
            findings.append(Finding(False, "peers_not_right", "配下拠点が右の列に無い"))
        xs = [n.x for n in below]
        if max(xs) - min(xs) < 80:
            findings.append(Finding(True, "peer_column", "配下拠点の x が揃っている"))
        else:
            findings.append(Finding(False, "peer_not_column", "配下拠点が列になっていない"))

    return findings


def is_acceptable(findings: list[Finding]) -> bool:
    return bool(findings) and all(item.ok for item in findings)
