"""公開学習用の構成図の評価と、NetVision が出す図が同じ読み方になること。"""

from pathlib import Path

from app.services import svglayout
from app.services.topology import get_graph, load_diagram, scope_graph
from app.services.topology_export import export_svg, render_svg
from app.services.topology_quality import (
    evaluate_site_diagram,
    evaluate_svg,
    is_acceptable,
    score_diagram,
)
from tests import svgread
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
    scored = score_diagram(graph, svg)
    assert scored.total > 100, f"{scored.total}点 {scored.parts}"

    exported = export_svg(
        db_session, "physical", site_id=ids["branch"], title="関西支社 物理構成", style="simple"
    )
    assert not _has_diagonal(exported), "書き出した図の線が斜め"
    for hostname in placed:
        assert hostname in exported
    screen = load_diagram(db_session, "physical", site_id=ids["branch"])
    assert {n.hostname for n in screen.nodes} == {n.hostname for n in graph.nodes}


def test_port_names_are_not_written_on_the_wires(db_session):
    """ポート名が線の上に乗っていないこと。

    文字は白で縁取ってあるので、線の上に置くとその線が切れて見える。
    自分が出ている口の1区間だけは重なってよい（そこに寄り添って初めて
    どの機器のどの口か分かる）ので、またぐ区間が2つ以上あるものを不合格とする。
    """
    import re

    ids = build_center(db_session)
    graph = scope_graph(get_graph(db_session), site_id=ids["branch"])
    svg = render_svg(graph, title="関西支社 物理構成")

    segments = []
    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split(" ")]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            segments.append(
                svglayout.Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)).grow(1.5)
            )

    on_wires = [
        rect
        for rect in svgread.rects(svg, "port-label")
        if sum(1 for seg in segments if rect.overlaps(seg)) > 1
    ]
    assert not on_wires, f"線の上に乗っているポート名 {len(on_wires)} 個"


def _has_diagonal(svg: str) -> bool:
    import re

    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split(" ")]
        for a, b in zip(pts, pts[1:], strict=False):
            if abs(a[0] - b[0]) > 8 and abs(a[1] - b[1]) > 8:
                return True
    return False


def test_the_exported_boxes_sit_where_the_screen_draws_them(db_session):
    """画面と書き出しで、箱の位置がそろっていること。

    同じ台帳から出した2つの図で箱が上下にずれると、線の付き方も変わる。
    「画面では通っているのに、貼った図では通っていない」の元になる。
    画面は基準より小さい箱（対向の簡易表示）を上に寄せたまま描くので、
    書き出しも同じにする。中心をそろえると、そこだけ 12px 下がる。
    """
    import re

    ids = build_center(db_session)
    graph = load_diagram(db_session, "physical", site_id=ids["branch"])
    svg = render_svg(graph, title="関西支社 物理構成")

    boxes = [
        (float(x), float(y), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="([\d.\-]+)" y="([\d.\-]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
        if float(w) > 100
    ]
    nodes = list(graph.nodes)
    assert any(node.outside for node in nodes), "対向の機器が出ていない"
    assert len(boxes) == len(nodes), f"箱 {len(boxes)} 個 / 機器 {len(nodes)} 台"

    # 図全体のずれ（余白と凡例のぶん）。横は箱の大きさで変わらないので、ここから求める
    shift_x = min(bx for bx, _, _ in boxes) - min(node.x for node in nodes)

    # 縦のずれは、列ごとに上から順で突き合わせる。箱の高さが違うと、
    # 図全体を上から並べた順は機器の順と一致しない
    gaps = set()
    for x in {round(node.x) for node in nodes}:
        column = sorted((node for node in nodes if round(node.x) == x), key=lambda n: n.y)
        drawn = sorted((b for b in boxes if abs(b[0] - (x + shift_x)) < 1), key=lambda b: b[1])
        assert len(column) == len(drawn), f"x={x} の箱の数が合わない"
        for node, (_bx, by, height) in zip(column, drawn, strict=True):
            # 画面（shiftYFor）と同じ規則。広げた分だけ上へ寄せ、
            # 基準より小さい箱は動かさない
            gaps.add(round(by + max(0.0, (height - 78) / 2) - node.y))

    # ずれが1種類でなければ、機器によって上下がずれている
    assert len(gaps) == 1, f"箱ごとに縦のずれが違う: {sorted(gaps)}"
