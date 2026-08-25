"""画面で見ている図と、書き出した図が同じであること。

人は画面で配置と線を確かめてから「出力する」を押す。そこで別の図が出るなら、
確かめた意味が無い。書き出しが自分でもう一度線を引くと、同じ規則で書いて
あっても候補の選び方が少し違うだけで別の図になる。

そこで画面が引いた線をそのまま預かり、書き出しはそれを描くだけにする。
ここで見るのは、預けた線と箱が、そのまま図に出ているかどうか。
"""

import re

from app.services import topology_view
from app.services.topology import load_diagram
from app.services.topology_export import render_svg
from tests.conftest import API
from tests.test_topology_scope import build_center


def _polylines(svg: str) -> list[list[tuple[float, float]]]:
    return [
        [tuple(float(v) for v in point.split(",")) for point in found.split(" ")]
        for found in re.findall(r'<polyline points="([^"]+)"', svg)
    ]


def _boxes(svg: str) -> list[tuple[float, float, float, float]]:
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="([\d.\-]+)" y="([\d.\-]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
        if float(w) > 100
    ]


def test_the_line_the_screen_drew_is_the_line_that_comes_out(db_session):
    ids = build_center(db_session)
    graph = load_diagram(db_session, "physical", site_id=ids["branch"])
    edge = graph.edges[0]
    source = next(n for n in graph.nodes if n.device_id == edge.source)
    target = next(n for n in graph.nodes if n.device_id == edge.target)

    # 画面が「わざわざ遠回りして引いた」線。書き出しが自分で引き直すと、
    # まず選ばない形にしてある
    detour = [
        (source.x + 210.0, source.y + 26.0),
        (source.x + 900.0, source.y + 26.0),
        (source.x + 900.0, target.y + 26.0),
        (target.x + 210.0, target.y + 26.0),
    ]
    view = topology_view.ViewGeometry(
        wires={
            edge.id: topology_view.ViewWire(
                edge_id=edge.id, points=detour, source_side="right", target_side="right"
            )
        }
    )

    svg = render_svg(graph, title="試験", view=view)
    lines = _polylines(svg)
    # 図全体のずれ（余白と見出しのぶん）を、線の1本目から求める
    found = next(
        (line for line in lines if len(line) == len(detour)),
        None,
    )
    assert found is not None, f"預けた線が見当たらない: {lines}"
    dx = found[0][0] - detour[0][0]
    dy = found[0][1] - detour[0][1]
    for (px, py), (qx, qy) in zip(found, detour, strict=True):
        assert abs(px - (qx + dx)) < 1 and abs(py - (qy + dy)) < 1


def test_the_box_the_screen_drew_is_the_box_that_comes_out(db_session):
    ids = build_center(db_session)
    graph = load_diagram(db_session, "physical", site_id=ids["branch"])
    node = next(n for n in graph.nodes if not n.outside)

    view = topology_view.ViewGeometry(
        boxes={
            node.device_id: topology_view.ViewBox(
                device_id=node.device_id, x=node.x, y=node.y, width=260.0, height=120.0
            )
        }
    )
    svg = render_svg(graph, title="試験", view=view)
    assert any(abs(w - 260) < 1 and abs(h - 120) < 1 for _x, _y, w, h in _boxes(svg)), (
        f"預けた大きさの箱が無い: {_boxes(svg)}"
    )


def test_without_a_view_it_draws_the_lines_itself(db_session):
    """預かっていないときは、これまでどおり書き出し側が引く。"""
    ids = build_center(db_session)
    graph = load_diagram(db_session, "physical", site_id=ids["branch"])
    svg = render_svg(graph, title="試験")
    assert _polylines(svg), "線が1本も無い"


def test_the_screen_can_hand_over_what_it_drew(client, db_session):
    ids = build_center(db_session)
    graph = load_diagram(db_session, "physical", site_id=ids["branch"])
    edge = graph.edges[0]
    node = next(n for n in graph.nodes if not n.outside)

    kept = client.post(
        f"{API}/topology/views",
        json={
            "boxes": [
                {
                    "device_id": node.device_id,
                    "x": node.x,
                    "y": node.y,
                    "width": 240.0,
                    "height": 100.0,
                }
            ],
            "wires": [
                {
                    "edge_id": edge.id,
                    "points": [[0.0, 0.0], [500.0, 0.0], [500.0, 400.0]],
                    "source_side": "right",
                    "target_side": "top",
                }
            ],
        },
    )
    assert kept.status_code == 200
    token = kept.json()["token"]

    res = client.get(f"{API}/topology/export.svg", params={"site_id": ids["branch"], "view": token})
    assert res.status_code == 200
    svg = res.text
    assert any(abs(w - 240) < 1 and abs(h - 100) < 1 for _x, _y, w, h in _boxes(svg))
    # 折れ点が、預けたとおりの場所にあること。図全体のずれは、預けた箱から求める
    box = next((b for b in _boxes(svg) if abs(b[2] - 240) < 1 and abs(b[3] - 100) < 1), None)
    assert box is not None
    dx, dy = box[0] - node.x, box[1] - node.y
    corners = {(round(x), round(y)) for line in _polylines(svg) for x, y in line}
    assert (round(500 + dx), round(dy)) in corners, "預けた折れ点が図に無い"
    assert (round(500 + dx), round(400 + dy)) in corners, "預けた終わりの点が図に無い"


def test_a_token_we_do_not_know_is_ignored(client, db_session):
    """札が古くなっていても、図は出す。出ないほうが困る。"""
    ids = build_center(db_session)
    res = client.get(
        f"{API}/topology/export.svg", params={"site_id": ids["branch"], "view": "ないよ"}
    )
    assert res.status_code == 200
    assert "<svg" in res.text
