"""公開学習用の構成図の評価と、NetVision が出す図が同じ読み方になること。"""

from pathlib import Path

from app.services.topology import get_graph, load_diagram, scope_graph
from app.services.topology_export import export_svg, render_svg
from app.services.topology_quality import evaluate_site_diagram, evaluate_svg, is_acceptable
from tests.test_topology_scope import build_center

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "topology-samples"


def test_kansai_branch_diagram_is_readable(db_session):
    """関西支社を見ると、見本と同じ読み方の図になること。"""
    ids = build_center(db_session)
    graph = scope_graph(get_graph(db_session), site_id=ids["branch"])
    findings = evaluate_site_diagram(graph)
    failed = [item.detail for item in findings if not item.ok]
    assert is_acceptable(findings), failed

    placed = {n.hostname: n for n in graph.nodes}
    svg = render_svg(graph, title="関西支社 物理構成", style="simple")
    out = SAMPLES / "netvision-kansai.svg"
    out.write_text(svg, encoding="utf-8")
    svg_findings = evaluate_svg(svg, graph)
    failed_svg = [item.detail for item in svg_findings if not item.ok]
    assert is_acceptable(svg_findings), failed_svg
    assert "west-core-rt-02" in svg
    assert abs(placed["west-core-rt-02"].y - placed["west-core-rt-01"].y) < 1
    assert placed["west-core-rt-01"].y < placed["ksi-br-rt-01"].y
    assert placed["kyoto-rt-01"].x > placed["ksi-br-rt-01"].x + 80

    exported = export_svg(
        db_session, "physical", site_id=ids["branch"], title="関西支社 物理構成", style="simple"
    )
    assert not _has_diagonal(exported), "書き出した図の線が斜め"
    for hostname in placed:
        assert hostname in exported
    screen = load_diagram(db_session, "physical", site_id=ids["branch"])
    assert {n.hostname for n in screen.nodes} == {n.hostname for n in graph.nodes}


def _has_diagonal(svg: str) -> bool:
    import re

    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split(" ")]
        for a, b in zip(pts, pts[1:], strict=False):
            if abs(a[0] - b[0]) > 8 and abs(a[1] - b[1]) > 8:
                return True
    return False
