"""結線の形を変えても、同じ規則で資料として読める図になること。

特定の拠点の見本に寄せない。星型、二重化、鎖、輪、島が割れても、
箱が重ならず、線は縦横だけで、冗長は横並び、点数は100を超える。
"""

from __future__ import annotations

import random

from app.models.device import DeviceStatus, DeviceType
from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import (
    TopologyEdgeView,
    TopologyGraph,
    TopologyNodeView,
    _compact,
)
from app.services.topology_export import render_svg
from app.services.topology_quality import (
    evaluate_site_diagram,
    evaluate_svg,
    is_acceptable,
    score_diagram,
)


def node(
    device_id: int,
    hostname: str,
    *,
    kind: str = "rt",
    site_id: int | None = 1,
    outside: bool = False,
) -> TopologyNodeView:
    device_type = {
        "rt": DeviceType.ROUTER,
        "sw": DeviceType.SWITCH,
        "fw": DeviceType.FIREWALL,
    }.get(kind, DeviceType.ROUTER)
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="X",
        device_type=str(device_type),
        status=str(DeviceStatus.ACTIVE),
        site_id=site_id,
        site_name=None if outside else "site",
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=0,
        x=0,
        y=0,
        placed=False,
        outside=outside,
    )


def edge(edge_id: int, source: int, target: int, port: int | None = None) -> TopologyEdgeView:
    number = port if port is not None else edge_id
    return TopologyEdgeView(
        id=edge_id,
        source=source,
        target=target,
        source_interface=f"GigabitEthernet0/{number}",
        target_interface="GigabitEthernet0/0",
        origin=LinkOrigin.DISCOVERED,
        protocol=None,
        label=None,
        confirmed_both_ways=True,
    )


def _layout(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView],
    *,
    inside: set[int] | None = None,
) -> tuple[TopologyGraph, str]:
    world = TopologyGraph(layer="physical", nodes=nodes, edges=edges)
    _compact(nodes, edges, inside=inside, world=world if inside else None)
    graph = TopologyGraph(layer="physical", nodes=nodes, edges=edges)
    svg = render_svg(graph, title="構成", style="simple")
    return graph, svg


def _must_read(graph: TopologyGraph, svg: str, title: str) -> None:
    findings = evaluate_site_diagram(graph)
    failed = [item.detail for item in findings if not item.ok]
    assert is_acceptable(findings), f"{title}: {failed}"
    svg_findings = evaluate_svg(svg, graph)
    failed_svg = [item.detail for item in svg_findings if not item.ok]
    assert is_acceptable(svg_findings), f"{title} SVG: {failed_svg}"
    scored = score_diagram(graph, svg)
    assert scored.total > 100, f"{title} {scored.total}点 {scored.parts}"


def test_a_chain_stays_a_spine():
    nodes = [
        node(1, "a-rt", kind="rt"),
        node(2, "b-fw", kind="fw"),
        node(3, "c-sw", kind="sw"),
        node(4, "d-sw", kind="sw"),
        node(5, "e-sw", kind="sw"),
    ]
    edges = [edge(i, i, i + 1) for i in range(1, 5)]
    graph, svg = _layout(nodes, edges)
    _must_read(graph, svg, "鎖")
    by_id = {n.hostname: n for n in nodes}
    assert by_id["a-rt"].y < by_id["b-fw"].y < by_id["c-sw"].y < by_id["d-sw"].y
    xs = [n.x for n in nodes]
    assert max(xs) - min(xs) < 1


def test_a_star_keeps_port_order_on_the_row():
    hub = node(1, "hub-rt")
    leaves = [node(10 + i, f"leaf-{i:02d}-rt") for i in range(6)]
    # 名前の順とポートの順を食い違わせる
    ports = [5, 1, 4, 2, 6, 3]
    edges = [edge(i + 1, 1, leaf.device_id, port=ports[i]) for i, leaf in enumerate(leaves)]
    graph, svg = _layout([hub, *leaves], edges)
    _must_read(graph, svg, "星型")
    assert all(leaf.y > hub.y + 80 for leaf in leaves)
    by_port = [leaf for _, leaf in sorted(zip(ports, leaves, strict=True))]
    xs = [leaf.x for leaf in by_port]
    assert xs == sorted(xs)


def test_dual_homed_leaves_sit_under_the_pair():
    cores = [node(1, "core-a-sw", kind="sw"), node(2, "core-b-sw", kind="sw")]
    leaves = [node(10 + i, f"acc-{i:02d}-sw", kind="sw") for i in range(4)]
    edges = [edge(1, 1, 2), edge(2, 1, 2)]
    n = 3
    for leaf in leaves:
        edges.append(edge(n, 1, leaf.device_id, port=n))
        n += 1
        edges.append(edge(n, 2, leaf.device_id, port=n))
        n += 1
    graph, svg = _layout([*cores, *leaves], edges)
    _must_read(graph, svg, "両方に繋がる配下")
    assert abs(cores[0].y - cores[1].y) < 1
    assert abs(cores[0].x - cores[1].x) >= 200
    assert all(leaf.y > cores[0].y + 80 for leaf in leaves)
    core_mid = (cores[0].x + cores[1].x) / 2
    leaf_mid = sum(leaf.x for leaf in leaves) / len(leaves)
    assert abs(core_mid - leaf_mid) < 130


def test_single_homed_leaves_stay_under_their_own_parent():
    cores = [node(1, "core-a-sw", kind="sw"), node(2, "core-b-sw", kind="sw")]
    left = [node(10, "acc-a1-sw", kind="sw"), node(11, "acc-a2-sw", kind="sw")]
    right = [node(20, "acc-b1-sw", kind="sw"), node(21, "acc-b2-sw", kind="sw")]
    edges = [
        edge(1, 1, 2),
        edge(2, 1, 2),
        edge(3, 1, 10, port=1),
        edge(4, 1, 11, port=2),
        edge(5, 2, 20, port=1),
        edge(6, 2, 21, port=2),
    ]
    graph, svg = _layout([*cores, *left, *right], edges)
    _must_read(graph, svg, "片系ずつの配下")
    assert abs(cores[0].y - cores[1].y) < 1
    left_mid = sum(n.x for n in left) / 2
    right_mid = sum(n.x for n in right) / 2
    assert left_mid < right_mid
    assert abs(left_mid - cores[0].x) < abs(left_mid - cores[1].x)
    assert abs(right_mid - cores[1].x) < abs(right_mid - cores[0].x)


def test_two_islands_do_not_overlap():
    left = [node(1, "a-rt"), node(2, "a-sw", kind="sw")]
    right = [node(10, "b-rt", site_id=2), node(11, "b-sw", kind="sw", site_id=2)]
    edges = [edge(1, 1, 2), edge(2, 10, 11)]
    graph, svg = _layout([*left, *right], edges)
    _must_read(graph, svg, "島が二つ")
    boxes = [(n.x, n.y) for n in graph.nodes]
    assert len(set(boxes)) == 4


def test_a_ring_is_still_orthogonal_and_readable():
    nodes = [node(i, f"r{i}-rt") for i in range(1, 6)]
    edges = [edge(i, i, i + 1 if i < 5 else 1) for i in range(1, 6)]
    graph, svg = _layout(nodes, edges)
    _must_read(graph, svg, "輪")


def test_site_view_with_uplinks_and_many_peer_sites():
    hub = node(1, "hub-rt", site_id=1)
    sw = node(2, "hub-sw", kind="sw", site_id=1)
    up_a = node(10, "up-a-rt", site_id=2, outside=True)
    up_b = node(11, "up-b-rt", site_id=2, outside=True)
    peers = []
    edges = [
        edge(1, 1, 2),
        edge(2, 1, 10, port=1),
        edge(3, 1, 11, port=2),
        edge(4, 10, 11),
    ]
    n = 5
    for site in range(3, 8):
        a = node(100 + site, f"p{site}-a-rt", site_id=site, outside=True)
        peers.append(a)
        edges.append(edge(n, 1, a.device_id, port=n))
        n += 1
        if site % 2 == 0:
            b = node(200 + site, f"p{site}-b-rt", site_id=site, outside=True)
            peers.append(b)
            edges.append(edge(n, 1, b.device_id, port=n))
            n += 1
    nodes = [hub, sw, up_a, up_b, *peers]
    graph, svg = _layout(nodes, edges, inside={1, 2})
    _must_read(graph, svg, "拠点から見た対向")
    assert up_a.y < hub.y and up_b.y < hub.y
    assert abs(up_a.y - up_b.y) < 1
    assert all(p.x > hub.x + 80 for p in peers)
    by_site: dict[int, list[TopologyNodeView]] = {}
    for peer in peers:
        by_site.setdefault(peer.site_id or 0, []).append(peer)
    lefts = [min(n.x for n in members) for members in by_site.values()]
    assert max(lefts) - min(lefts) < 80


def test_random_layered_networks_stay_readable():
    """階層のある物理網を乱数で組んでも、同じ規則で読める。"""
    for seed in range(48):
        rng = random.Random(seed)
        nodes, edges = _random_layered(rng, seed)
        graph, svg = _layout(nodes, edges)
        _must_read(graph, svg, f"乱数 seed={seed}")


def _random_layered(
    rng: random.Random, seed: int
) -> tuple[list[TopologyNodeView], list[TopologyEdgeView]]:
    cores = rng.choice((1, 2))
    access = rng.randint(2, 7)
    has_fw = rng.choice((True, False))
    nodes: list[TopologyNodeView] = []
    edges: list[TopologyEdgeView] = []
    next_id = 1
    edge_id = 1

    def add(hostname: str, kind: str) -> TopologyNodeView:
        nonlocal next_id
        item = node(next_id, hostname, kind=kind)
        next_id += 1
        nodes.append(item)
        return item

    core_nodes = [add(f"c{i}-sw", "sw") for i in range(cores)]
    if cores == 2:
        edges.append(edge(edge_id, core_nodes[0].device_id, core_nodes[1].device_id))
        edge_id += 1
        if rng.choice((True, False)):
            edges.append(edge(edge_id, core_nodes[0].device_id, core_nodes[1].device_id))
            edge_id += 1

    parent_row = core_nodes
    if has_fw:
        fw = add("fw-01", "fw")
        for core in core_nodes:
            edges.append(edge(edge_id, fw.device_id, core.device_id, port=edge_id))
            edge_id += 1
        if rng.choice((True, False)):
            edge_rt = add("edge-rt", "rt")
            edges.append(edge(edge_id, edge_rt.device_id, fw.device_id))
            edge_id += 1

    for index in range(access):
        acc = add(f"a{index:02d}-sw", "sw")
        targets = list(parent_row)
        rng.shuffle(targets)
        homed = targets[: rng.choice((1, len(targets)))]
        for target in homed:
            edges.append(edge(edge_id, target.device_id, acc.device_id, port=edge_id))
            edge_id += 1
    return nodes, edges
