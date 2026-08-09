"""自動生成した図で、文字が何かに被っていないこと。

人が手で直した結果なら、その人が見て判断している。
機械が出したものが被っているのは道具側の不備なので、ここは緩めない。

SVG を読み直して、実際に描かれた矩形どうしの重なりを見る。
生成側と検証側を別の経路にしてあるので、置き方を変えても検証は効く。
"""

import re

import pytest

from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.svglayout import Rect, text_rect, text_width
from app.services.topology import TopologyEdgeView, TopologyGraph, TopologyNodeView
from app.services.topology_export import NODE_H, NODE_W, render_svg
from tests import svgread

_RECT = re.compile(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"')


def node(device_id: int, hostname: str, x: float, y: float) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="C9300-48P",
        device_type="switch",
        status="active",
        site_id=1,
        site_name="本社",
        mgmt_ip="10.0.0.1",
        severity=Severity.OK,
        interface_count=4,
        x=x,
        y=y,
        placed=True,
        port_kinds=["GigabitEthernet"],
    )


def edge(edge_id: int, source: int, target: int, a: str, b: str) -> TopologyEdgeView:
    return TopologyEdgeView(
        id=edge_id,
        source=source,
        target=target,
        source_interface=a,
        target_interface=b,
        origin=LinkOrigin.DISCOVERED,
        protocol="lldp",
        label=None,
        confirmed_both_ways=True,
    )


def node_rects(svg: str) -> list[Rect]:
    """機器の箱と、絞り込みの外にいる相手の箱。色帯（幅5）は除く。"""
    from app.services.topology_export import PEER_H, PEER_W

    out: list[Rect] = []
    for match in _RECT.finditer(svg):
        x, y, w, h = (float(v) for v in match.groups())
        is_node = w >= NODE_W - 1 and h >= NODE_H - 1
        is_peer = abs(w - PEER_W) < 1 and abs(h - PEER_H) < 1
        if is_node or is_peer:
            out.append(Rect(x, y, x + w, y + h))
    return out


def assert_no_overlap(svg: str) -> None:
    labels = svgread.rects(svg, "port-label")
    boxes = node_rects(svg)
    assert labels, "ポート名が1つも描かれていない"

    for label in labels:
        for box in boxes:
            assert not label.overlaps(box), f"ラベルが機器の箱に重なっている: {label} / {box}"

    for i, first in enumerate(labels):
        for second in labels[i + 1 :]:
            assert not first.overlaps(second), f"ラベルどうしが重なっている: {first} / {second}"


def grid(count: int, columns: int = 3) -> TopologyGraph:
    """格子状に並べて、隣どうしを全部つなぐ。線が密になる最悪に近い形。"""
    nodes = [
        node(i + 1, f"sw-{i + 1:02d}", (i % columns) * 260.0, (i // columns) * 170.0)
        for i in range(count)
    ]
    edges: list[TopologyEdgeView] = []
    for i in range(count):
        for j in (i + 1, i + columns):
            if j < count and (j == i + columns or (i + 1) % columns):
                edges.append(
                    edge(
                        len(edges) + 1,
                        i + 1,
                        j + 1,
                        f"GigabitEthernet1/0/{i + 1}",
                        f"GigabitEthernet1/0/{j + 1}",
                    )
                )
    return TopologyGraph(layer="physical", nodes=nodes, edges=edges)


def test_two_nodes_side_by_side():
    assert_no_overlap(render_svg(grid(2, columns=2)))


def test_dense_grid():
    """格子状に全部つないだ状態でも被らないこと。"""
    assert_no_overlap(render_svg(grid(9)))


def test_dense_grid_with_long_names():
    assert_no_overlap(render_svg(grid(9), port_style="full"))


def test_dense_grid_along_line():
    assert_no_overlap(render_svg(grid(9), along_line=True))


def test_hub_and_spoke():
    """1台から何本も出ている形。同じ場所にラベルが集まりやすい。"""
    nodes = [node(1, "core-sw-01", 400.0, 400.0)]
    edges: list[TopologyEdgeView] = []
    import math

    for index in range(8):
        angle = math.radians(index * 45)
        nodes.append(
            node(
                index + 2,
                f"acc-sw-{index:02d}",
                400 + 340 * math.cos(angle),
                400 + 300 * math.sin(angle),
            )
        )
        edges.append(
            edge(
                index + 1,
                1,
                index + 2,
                f"GigabitEthernet1/0/{index + 1}",
                "GigabitEthernet0/1",
            )
        )

    assert_no_overlap(render_svg(TopologyGraph(layer="physical", nodes=nodes, edges=edges)))


@pytest.mark.parametrize("style", ["short", "number", "full"])
def test_every_port_style(style):
    assert_no_overlap(render_svg(grid(6), port_style=style))


def test_text_width_counts_full_width_characters_as_double():
    """日本語が入るラベルで幅を見誤ると、当たり判定そのものが効かなくなる。"""
    assert text_width("abcd", 10) == pytest.approx(text_width("あい", 10))


def test_rotated_rect_covers_the_rotated_text():
    """回転させた文字の外接矩形が、回す前より広いこと。"""
    flat = text_rect(0, 0, "Gi1/0/24", 10, "start", 0)
    tilted = text_rect(0, 0, "Gi1/0/24", 10, "start", 45)
    assert tilted.y2 - tilted.y1 > flat.y2 - flat.y1


def test_random_layouts_never_overlap():
    """乱数で配置を振っても被らないこと。

    特定の並びだけで通っても意味が無い。現場の配置は人が動かした結果なので、
    整った格子にはならない。
    """
    import random

    rng = random.Random(20260809)
    for _ in range(200):
        count = rng.randint(2, 9)
        # 自動配置と同じくらいの間隔を空ける。人が箱同士をくっつくまで
        # 寄せた場合はラベルの置き場所自体が無くなるが、それは人の判断であって
        # ここで保証する対象ではない
        placed: list[tuple[float, float]] = []
        while len(placed) < count:
            spot = (rng.uniform(0, 1200), rng.uniform(0, 900))
            if all(
                abs(spot[0] - other[0]) > NODE_W + 70 or abs(spot[1] - other[1]) > NODE_H + 70
                for other in placed
            ):
                placed.append(spot)
        nodes = [node(i + 1, f"sw-{i + 1:02d}", x, y) for i, (x, y) in enumerate(placed)]
        edges = [
            edge(
                i,
                rng.randint(1, count),
                rng.randint(1, count),
                f"GigabitEthernet1/0/{rng.randint(1, 48)}",
                f"TenGigabitEthernet{rng.randint(1, 4)}/1/{rng.randint(1, 8)}",
            )
            for i in range(1, rng.randint(2, 12))
        ]
        edges = [e for e in edges if e.source != e.target]
        if not edges:
            continue

        graph = TopologyGraph(layer="physical", nodes=nodes, edges=edges)
        style = rng.choice(["short", "number", "full"])
        along = rng.choice([True, False])
        assert_no_overlap(render_svg(graph, port_style=style, along_line=along))


def test_auto_layout_output_is_clean():
    """自動配置した図は必ず綺麗に出ること。

    人が動かした結果は人の判断だが、機械が置いた図で被っているのは道具の不備。
    自動配置は種別ごとに段を作るので、段の中と段をまたぐ線の両方が出る。
    """
    from app.services.topology import _auto_layout

    class FakeDevice:
        def __init__(self, device_id: int, hostname: str, device_type):
            self.id, self.hostname, self.device_type = device_id, hostname, device_type

    from app.models import DeviceType

    types = [
        DeviceType.ROUTER,
        DeviceType.FIREWALL,
        DeviceType.SWITCH,
        DeviceType.SWITCH,
        DeviceType.SWITCH,
        DeviceType.WIRELESS_CONTROLLER,
        DeviceType.SERVER,
    ]
    for count in (2, 4, 7):
        devices = [
            FakeDevice(i + 1, f"dev-{i + 1:02d}", types[i % len(types)]) for i in range(count)
        ]
        positions = _auto_layout(devices)  # type: ignore[arg-type]

        nodes = [node(d.id, d.hostname, *positions[d.id]) for d in devices]
        edges = [
            edge(i, i, i + 1, f"GigabitEthernet1/0/{i}", f"TenGigabitEthernet1/1/{i}")
            for i in range(1, count)
        ]
        graph = TopologyGraph(layer="physical", nodes=nodes, edges=edges)

        for style in ("short", "number", "full"):
            assert_no_overlap(render_svg(graph, port_style=style))
            assert_no_overlap(render_svg(graph, port_style=style, along_line=True))


def test_labels_avoid_the_peer_boxes():
    """絞り込みの外にいる相手の箱にも、ラベルが被らないこと。"""
    nodes = [
        node(1, "core-sw-01", 0, 0),
        node(2, "acc-sw-01", 0, 300),
        node(3, "edge-rt-01", 400, 0),
        node(4, "edge-rt-02", 400, 300),
    ]
    for target in nodes[:2]:
        target.site_id = 1
    for target in nodes[2:]:
        target.site_id = 2

    edges = [
        edge(1, 1, 2, "GigabitEthernet1/0/1", "GigabitEthernet1/0/2"),
        edge(2, 1, 3, "TenGigabitEthernet1/1/1", "TenGigabitEthernet1/1/2"),
        edge(3, 2, 4, "TenGigabitEthernet1/1/3", "TenGigabitEthernet1/1/4"),
    ]
    graph = TopologyGraph(layer="physical", nodes=nodes, edges=edges)

    for style in ("short", "number", "full"):
        svg = render_svg(graph, site_id=1, port_style=style)
        assert "edge-rt-01" in svg  # 対向が残っている
        assert_no_overlap(svg)


def test_parallel_links_do_not_draw_on_top_of_each_other():
    """同じ機器同士を結ぶ線が、重なって1本に見えないこと。

    LAG や冗長リンクでは普通に起きる。図で1本に見えると、
    実際は2本あるのに1本だと読まれる。
    """
    import re

    nodes = [node(1, "core-sw-01", 0, 0), node(2, "core-sw-02", 400, 0)]
    edges = [
        edge(1, 1, 2, "TenGigabitEthernet1/1/1", "TenGigabitEthernet1/1/1"),
        edge(2, 1, 2, "TenGigabitEthernet1/1/2", "TenGigabitEthernet1/1/2"),
        edge(3, 1, 2, "TenGigabitEthernet1/1/3", "TenGigabitEthernet1/1/3"),
    ]
    svg = render_svg(TopologyGraph(layer="physical", nodes=nodes, edges=edges))

    lines = re.findall(r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"', svg)
    drawn = [tuple(line) for line in lines]
    assert len(drawn) == len(set(drawn)), f"線が重なっています: {drawn}"


def test_a_single_link_is_not_shifted():
    """1本しか無いときは、元の位置のまま引くこと。"""
    import re

    nodes = [node(1, "sw-a", 0, 0), node(2, "sw-b", 400, 0)]
    edges = [edge(1, 1, 2, "GigabitEthernet1/0/1", "GigabitEthernet1/0/2")]
    svg = render_svg(TopologyGraph(layer="physical", nodes=nodes, edges=edges))

    line = re.search(r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"', svg)
    assert line and line.group(2) == line.group(4)  # 水平のまま
