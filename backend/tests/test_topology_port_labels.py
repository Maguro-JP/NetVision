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
from tests import svgread


def node(device_id: int, hostname: str, x: float, y: float) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="C9300",
        device_type="switch",
        status="active",
        site_id=None,
        site_name=None,
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=1,
        x=x,
        y=y,
        placed=True,
        port_kinds=["GigabitEthernet"],
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


def test_site_filter_keeps_only_that_site():
    """拠点ごとの構成図が出せること。

    対象の機器だけを本体として描く。拠点外の相手は簡易表示で残す
    （消すと上位への接続が「無い」ように見えるため）。
    """
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].site_id, graph.nodes[0].site_name = 1, "本社"
    graph.nodes[1].site_id, graph.nodes[1].site_name = 2, "大阪支社"

    svg = render_svg(graph, site_id=2)
    assert "sw-b" in svg
    assert "大阪支社" in svg  # 見出しに拠点名が入る
    # 拠点外の機器は本体としては描かない（詳しい確認は専用のテストで行う）


def labels(svg: str) -> dict[str, tuple[float, float]]:
    return svgread.positions(svg)


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
    colors = {text: attrs["fill"] for text, attrs in svgread.elements(svg, "port-label")}
    assert colors["Gi1/0/1"] != colors["Te1/1/1"]


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


def test_device_box_carries_the_port_kind():
    """番号だけの表示にしても、機器の箱を見れば何のポートか分かること。

    色でも表せるが、色は凡例を探しに行かないと読めない。名前が機器の隣に
    あれば視線が動かない。
    """
    svg = render_svg(graph_of(0, 0, 400, 0), port_style="number")
    assert "GigabitEthernet" in svg
    # 線のラベルは番号だけになっている
    assert "Gi1/0/1" not in labels(svg)


def test_port_kind_is_coloured_like_its_labels():
    """機器の箱の種類名と、線のラベルの色が揃っていること。

    種類が複数あるとき、色が対応の手がかりになる。
    """
    svg = render_svg(graph_of(0, 0, 400, 0), port_style="number")
    kind = re.search(r'<tspan fill="([^"]+)">GigabitEthernet</tspan>', svg)
    colors = {text: attrs["fill"] for text, attrs in svgread.elements(svg, "port-label")}
    assert kind and kind.group(1) == colors["1/0/1"]


def test_peer_outside_the_filter_is_kept_as_a_stub():
    """絞り込みの外に出た対向を、簡易表示で残すこと。

    線ごと消すと、その拠点が「どこにも繋がっていない」図になる。
    実際には繋がっているので、相手がいることだけは残す。
    """
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].site_id, graph.nodes[0].site_name = 1, "本社"
    graph.nodes[1].site_id, graph.nodes[1].site_name = 2, "大阪支社"

    svg = render_svg(graph, site_id=2)

    assert "sw-a" in svg  # 相手の名前は出す
    assert "本社" in svg  # どこの機器かも出す
    assert "stroke-dasharray" in svg  # 破線の枠で、対象外だと分かるようにする
    # 図の中に残っている側のポート名は通常どおり出る
    assert "Te1/1/1" in svg


def test_peer_can_be_turned_off():
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].site_id = 1
    graph.nodes[1].site_id = 2

    svg = render_svg(graph, site_id=2, show_peers=False)
    assert "sw-a" not in svg


def test_peer_is_not_drawn_as_a_full_device():
    """簡易表示なので、型番や保守状態は出さない。本体と見分けが付くこと。"""
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].site_id, graph.nodes[0].model = 1, "C9500-40X"
    graph.nodes[1].site_id = 2

    svg = render_svg(graph, site_id=2)
    assert "C9500-40X" not in svg


def test_vlan_filter_also_keeps_the_peer():
    """VLAN で絞ったときも同じ。線の先が消えると経路が読めなくなる。"""
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].vlans = [10]
    graph.nodes[1].vlans = [20]

    svg = render_svg(graph, vlan=20)
    assert "sw-b" in svg
    assert "sw-a" in svg


def test_header_counts_the_lines_going_outside():
    """描いてある線が本数に入っていること。

    見出しが「0本」なのに線が3本ある図は、そこだけで信用を落とす。
    """
    graph = graph_of(0, 0, 400, 0)
    graph.nodes[0].site_id = 1
    graph.nodes[1].site_id = 2

    svg = render_svg(graph, site_id=2)
    assert "外部へ 1本" in svg
