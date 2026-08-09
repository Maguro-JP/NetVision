"""ポート名の置き方。

1本の線に2つのポート名を並べると「左が上位」のような規則を覚える必要が出る。
図の向きが変わればその規則は成り立たず、逆に読んでしまう。
**それぞれの機器側に置けば規則は要らない**ので、そこを確かめる。
"""

import re

from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import TopologyEdgeView, TopologyGraph, TopologyNodeView
from app.services.topology_export import NODE_H, NODE_W, render_svg

_TEXT = re.compile(r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>([^<]*)</text>')


def node(device_id: int, hostname: str, x: float, y: float) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="C9300",
        device_type="switch",
        status="active",
        site_name=None,
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=1,
        x=x,
        y=y,
        placed=True,
    )


def edge(source: int, target: int, a: str, b: str) -> TopologyEdgeView:
    return TopologyEdgeView(
        id=1,
        source=source,
        target=target,
        source_interface=a,
        target_interface=b,
        origin=LinkOrigin.DISCOVERED,
        protocol="lldp",
        label=None,
        confirmed_both_ways=True,
    )


def graph_of(ax: float, ay: float, bx: float, by: float) -> TopologyGraph:
    return TopologyGraph(
        layer="physical",
        nodes=[node(1, "sw-a", ax, ay), node(2, "sw-b", bx, by)],
        edges=[edge(1, 2, "GigabitEthernet1/0/1", "TenGigabitEthernet1/1/1")],
    )


def labels(svg: str) -> dict[str, tuple[float, float]]:
    return {m.group(3): (float(m.group(1)), float(m.group(2))) for m in _TEXT.finditer(svg)}


def centers(svg: str) -> dict[str, tuple[float, float]]:
    """機器名のテキスト位置から、そのノードのだいたいの中心を出す。"""
    found = labels(svg)
    return {
        "sw-a": (found["sw-a"][0] + NODE_W / 2 - 14, found["sw-a"][1] + NODE_H / 2 - 22),
        "sw-b": (found["sw-b"][0] + NODE_W / 2 - 14, found["sw-b"][1] + NODE_H / 2 - 22),
    }


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def assert_labels_sit_by_their_own_device(svg: str) -> None:
    found = labels(svg)
    node_at = centers(svg)

    # Gi1/0/1 は sw-a のポート、Te1/1/1 は sw-b のポート
    assert distance(found["Gi1/0/1"], node_at["sw-a"]) < distance(found["Gi1/0/1"], node_at["sw-b"])
    assert distance(found["Te1/1/1"], node_at["sw-b"]) < distance(found["Te1/1/1"], node_at["sw-a"])


def test_left_to_right():
    assert_labels_sit_by_their_own_device(render_svg(graph_of(0, 0, 400, 0)))


def test_right_to_left():
    """機器の並びが逆でも、ラベルは自分の機器側に付く。

    ここが「左が上位」規則の壊れるところ。位置で決めていれば影響を受けない。
    """
    assert_labels_sit_by_their_own_device(render_svg(graph_of(400, 0, 0, 0)))


def test_top_to_bottom():
    assert_labels_sit_by_their_own_device(render_svg(graph_of(0, 0, 0, 400)))


def test_bottom_to_top():
    assert_labels_sit_by_their_own_device(render_svg(graph_of(0, 400, 0, 0)))


def test_diagonal():
    assert_labels_sit_by_their_own_device(render_svg(graph_of(0, 0, 300, 300)))
    assert_labels_sit_by_their_own_device(render_svg(graph_of(300, 0, 0, 300)))


def test_both_labels_stay_off_the_line_and_apart():
    """機器の間隔が狭くても2つのラベルが重ならないこと。

    両端のラベルは線の反対側に出す。近づいても衝突しない。
    """
    svg = render_svg(graph_of(0, 0, 210, 0))  # ほとんど隙間が無い配置
    found = labels(svg)
    assert found["Gi1/0/1"][1] != found["Te1/1/1"][1]


def test_port_style_number_drops_the_prefix():
    """種別を色に移した分だけ文字を削る。"""
    svg = render_svg(graph_of(0, 0, 400, 0), port_style="number")
    found = labels(svg)
    assert "1/0/1" in found
    assert "Gi1/0/1" not in found


def test_port_style_full_keeps_the_whole_name():
    svg = render_svg(graph_of(0, 0, 400, 0), port_style="full")
    assert "GigabitEthernet1/0/1" in labels(svg)


def test_media_colours_are_applied():
    """1G と 10G が同じ色だと、色分けの意味が無い。"""
    svg = render_svg(graph_of(0, 0, 400, 0))
    giga = re.search(r'fill="([^"]+)"[^>]*>Gi1/0/1<', svg)
    ten = re.search(r'fill="([^"]+)"[^>]*>Te1/1/1<', svg)
    assert giga and ten and giga.group(1) != ten.group(1)


def test_legend_explains_the_colours():
    """凡例の無い色分けは、その図だけ見た人には読めない。"""
    svg = render_svg(graph_of(0, 0, 400, 0))
    assert "ポート種別" in svg
    assert "1G" in svg
    assert "10G" in svg


def test_along_line_never_renders_upside_down():
    """線に沿わせても、文字が上下逆さまになったら読めない。"""
    for ax, ay, bx, by in [(0, 0, 400, 0), (400, 0, 0, 0), (0, 0, 300, 300), (300, 300, 0, 0)]:
        svg = render_svg(graph_of(ax, ay, bx, by), along_line=True)
        for angle in re.findall(r"rotate\((-?[\d.]+)", svg):
            assert -90 <= float(angle) <= 90, f"{angle} 度は逆さま"


def test_manual_label_still_goes_in_the_middle():
    """人が付けた線の名前はポート名とは性質が違うので、中央のまま。"""
    graph = graph_of(0, 0, 400, 0)
    graph.edges[0].label = "本社⇔支社 専用線"
    svg = render_svg(graph)

    found = labels(svg)
    assert "本社⇔支社 専用線" in found
    assert "Gi1/0/1" not in found
