"""docs/topology-samples に書いた最適配置が、自動配置と一致すること。"""

from app.models.device import DeviceStatus, DeviceType
from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import TopologyEdgeView, TopologyGraph, TopologyNodeView, _compact


def node(device_id: int, hostname: str, *, outside: bool = False) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="X",
        device_type=str(DeviceType.ROUTER if "rt" in hostname else DeviceType.SWITCH),
        status=str(DeviceStatus.ACTIVE),
        site_id=None if outside else 1,
        site_name=None if outside else "支社",
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=0,
        x=0,
        y=0,
        placed=False,
        outside=outside,
    )


def edge(edge_id: int, source: int, target: int) -> TopologyEdgeView:
    return TopologyEdgeView(
        id=edge_id,
        source=source,
        target=target,
        source_interface=f"GigabitEthernet0/{edge_id}",
        target_interface="GigabitEthernet0/0",
        origin=LinkOrigin.DISCOVERED,
        protocol=None,
        label=None,
        confirmed_both_ways=True,
    )


def test_optimal_branch_puts_uplink_above_and_offices_in_a_right_column():
    """支社図は、センター真上・自拠点は縦・営業所は右の列。"""
    center = node(1, "center-rt", outside=True)
    hub = node(2, "br-rt")
    sw = node(3, "br-sw")
    offices = [node(10 + i, f"office-{i}-rt", outside=True) for i in range(3)]
    nodes = [center, hub, sw, *offices]
    edges = [
        edge(1, 1, 2),
        edge(2, 2, 3),
        edge(3, 2, 10),
        edge(4, 2, 11),
        edge(5, 2, 12),
    ]
    rank = {1: 0, 2: 1, 3: 2, 10: 2, 11: 2, 12: 2}
    _compact(nodes, edges, rank=rank, inside={2, 3})

    assert center.y < hub.y < sw.y
    assert abs(hub.x - sw.x) < 1
    xs = {office.x for office in offices}
    assert len(xs) == 1
    assert offices[0].x > hub.x + 100
    ys = sorted(office.y for office in offices)
    assert ys[1] - ys[0] > 40
    assert ys[2] - ys[1] > 40
    # 営業所を自拠点の真下に置かない（上り線を横切る）
    assert min(office.y for office in offices) < sw.y + 80


def test_redundant_cores_sit_side_by_side_not_stacked():
    """冗長のコア2台は同じ段に横並び。親子に積まない。"""
    cores = [node(1, "core-a-rt"), node(2, "core-b-rt")]
    branches = [node(10 + i, f"br-{i}-rt") for i in range(3)]
    nodes = [*cores, *branches]
    edges = [
        edge(1, 1, 2),
        edge(2, 1, 10),
        edge(3, 1, 11),
        edge(4, 1, 12),
        edge(5, 2, 10),
        edge(6, 2, 11),
        edge(7, 2, 12),
    ]
    _compact(nodes, edges)
    assert abs(cores[0].y - cores[1].y) < 1
    assert abs(cores[0].x - cores[1].x) >= 200
    for branch in branches:
        assert branch.y > cores[0].y + 80


def test_crosslinked_cores_sit_side_by_side_without_shared_children():
    """束ねたコアは、配下が片系ずつでも横並び。親子に積まない。"""
    fw = node(1, "fw")
    cores = [node(2, "core-a-sw"), node(3, "core-b-sw")]
    acc = node(4, "acc-sw")
    wlc = node(5, "wlc-sw")
    nodes = [fw, *cores, acc, wlc]
    edges = [
        edge(1, 1, 2),
        edge(2, 2, 3),
        edge(3, 2, 3),
        edge(4, 2, 4),
        edge(5, 3, 5),
    ]
    _compact(nodes, edges)
    assert abs(cores[0].y - cores[1].y) < 1
    assert abs(cores[0].x - cores[1].x) >= 200


def test_hq_puts_wan_edge_above_redundant_cores():
    """本社図は出口が上、冗長コアは横並び、対向は右。"""
    edge_rt = node(1, "edge-rt")
    fw = node(2, "fw")
    cores = [node(3, "core-a-sw"), node(4, "core-b-sw")]
    acc = node(5, "acc-sw")
    east = node(10, "east-rt", outside=True)
    west = node(11, "west-rt", outside=True)
    nodes = [edge_rt, fw, *cores, acc, east, west]
    edges = [
        edge(1, 1, 10),
        edge(2, 1, 11),
        edge(3, 1, 2),
        edge(4, 2, 3),
        edge(5, 2, 4),
        edge(6, 3, 4),
        edge(7, 3, 4),
        edge(8, 3, 5),
    ]
    inside = {1, 2, 3, 4, 5}
    _compact(
        nodes,
        edges,
        inside=inside,
        world=TopologyGraph(layer="", nodes=nodes, edges=edges),
    )
    assert edge_rt.y < fw.y < cores[0].y
    assert abs(cores[0].y - cores[1].y) < 1
    assert abs(cores[0].x - cores[1].x) >= 200
    assert acc.y > cores[0].y
    assert min(east.x, west.x) > max(edge_rt.x, fw.x, cores[0].x, cores[1].x, acc.x)
