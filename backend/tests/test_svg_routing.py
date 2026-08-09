"""線が、関係の無い機器の箱の上を通っていないこと。

図の上では「通過している線」と「その機器に繋がっている線」を見分けられない。
専用線が FW の箱を貫いていれば、FW を経由していると読まれる。**繋がっていない
ものが繋がって見える**のは、図として間違っている。

生成した SVG の polyline を読み直して、各区間が箱を横切っていないかを見る。
"""

import random
import re

from app.services.svglayout import Rect, segment_hits
from app.services.topology_export import NODE_H, NODE_W, render_svg
from tests.test_svg_collisions import edge, grid, node

_POLYLINE = re.compile(r'<polyline points="([^"]+)"')
_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"')


def segments(svg: str) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    out = []
    for match in _POLYLINE.finditer(svg):
        points = [
            (float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split(" ")
        ]
        out.extend(zip(points, points[1:], strict=False))
    return out


def node_boxes(svg: str) -> list[Rect]:
    return [
        Rect(float(x), float(y), float(x) + float(w), float(y) + float(h))
        for x, y, w, h in _RECT.findall(svg)
        if float(w) >= NODE_W - 1 and float(h) >= NODE_H - 1
    ]


def assert_no_pass_through(svg: str) -> None:
    """箱の内側を横切る区間が無いこと。

    端点は機器の中心なので、その箱だけは当たって当然。少し縮めた矩形で見て、
    端点を含む箱は除外する。
    """
    boxes = node_boxes(svg)
    assert boxes, "機器の箱が1つも描かれていない"

    for p1, p2 in segments(svg):
        for box in boxes:
            inner = box.grow(-4.0)
            holds_end = any(box.x1 <= x <= box.x2 and box.y1 <= y <= box.y2 for x, y in (p1, p2))
            if holds_end:
                continue
            assert not segment_hits(inner, p1, p2), f"線が機器の箱を通っている: {p1}-{p2} / {box}"


def test_a_line_does_not_pass_through_an_unrelated_device():
    """間に別の機器がいる2台を結ぶ。直線なら必ず貫く配置。"""
    nodes = [
        node(1, "rt-01", 0.0, 0.0),
        node(2, "fw-01", 400.0, 0.0),  # 中心が一直線上に来るように置く
        node(3, "sw-01", 800.0, 0.0),
    ]
    graph = grid(0)
    graph = type(graph)(
        layer="physical",
        nodes=nodes,
        edges=[edge(1, 1, 3, "GigabitEthernet0/0/0", "GigabitEthernet1/0/1")],
    )
    svg = render_svg(graph)
    assert_no_pass_through(svg)
    # 迂回させた結果、線は折れているはず
    assert len(segments(svg)) >= 2


def test_dense_layouts_stay_clean():
    for count in (4, 6, 9):
        assert_no_pass_through(render_svg(grid(count)))


def test_random_layouts_never_pass_through():
    rng = random.Random(20260809)
    for _ in range(80):
        count = rng.randint(3, 7)
        # 格子の升目に散らす。箱どうしが重なる配置は、そもそも避けようが無いので
        # ここでは作らない（自動配置は重ねない）
        cells = rng.sample([(c, r) for c in range(4) for r in range(3)], count)
        nodes = [
            node(
                i + 1,
                f"sw-{i + 1:02d}",
                cell[0] * 280.0 + rng.uniform(0, 40),
                cell[1] * 200.0 + rng.uniform(0, 40),
            )
            for i, cell in enumerate(cells)
        ]
        edges = [
            edge(
                i,
                rng.randint(1, count),
                rng.randint(1, count),
                f"GigabitEthernet1/0/{i}",
                f"GigabitEthernet1/0/{i + 1}",
            )
            for i in range(1, rng.randint(2, 6))
        ]
        edges = [e for e in edges if e.source != e.target]
        if not edges:
            continue
        graph = type(grid(0))(layer="physical", nodes=nodes, edges=edges)
        assert_no_pass_through(render_svg(graph))
