"""拠点がたくさんある全体図が、資料として読める形に収まること。

1つの木として組んでいたときは、120台が8段70列・幅16000px の帯になり、
画面に収めると線も名前も読めなかった。ここで見るのは、その形に戻って
いないこと。見た目の好みではなく、満たすか満たさないかの条件だけを見る。
"""

import itertools

from app.models.device import DeviceStatus, DeviceType
from app.models.topology import LinkOrigin
from app.services import topology_world
from app.services.lifecycle import Severity
from app.services.topology import (
    MIN_BOX_GAP,
    TopologyEdgeView,
    TopologyNodeView,
    _busiest_sides,
    _visual_bottom,
    _visual_left,
    _visual_right,
    _visual_top,
)


def _node(device_id: int, hostname: str, site_id: int, site_name: str) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="C9300",
        device_type=str(DeviceType.ROUTER),
        status=str(DeviceStatus.ACTIVE),
        site_id=site_id,
        site_name=site_name,
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=0,
        x=0.0,
        y=0.0,
        placed=False,
    )


def _edge(edge_id: int, source: int, target: int) -> TopologyEdgeView:
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


def _world(branches: int = 4, offices_each: int = 6):
    """センター2台 → 支社 → 営業所、という現場でよくある形を組む。"""
    nodes: list[TopologyNodeView] = []
    edges: list[TopologyEdgeView] = []
    next_id = 1
    edge_id = 1

    def add(hostname: str, site_id: int, site_name: str) -> TopologyNodeView:
        nonlocal next_id
        item = _node(next_id, hostname, site_id, site_name)
        next_id += 1
        nodes.append(item)
        return item

    cores = [add(f"core-rt-{i:02d}", 1, "センター") for i in range(1, 3)]
    edges.append(_edge(edge_id, cores[0].device_id, cores[1].device_id))
    edge_id += 1

    site_id = 2
    for b in range(branches):
        branch = add(f"br{b}-rt-01", site_id, f"{b}支社")
        branch_sw = add(f"br{b}-sw-01", site_id, f"{b}支社")
        edges.append(_edge(edge_id, branch.device_id, branch_sw.device_id))
        edge_id += 1
        for core in cores:
            edges.append(_edge(edge_id, core.device_id, branch.device_id))
            edge_id += 1
        site_id += 1
        for o in range(offices_each):
            office = add(f"br{b}-of{o}-rt-01", site_id, f"{b}-{o}営業所")
            edges.append(_edge(edge_id, branch.device_id, office.device_id))
            edge_id += 1
            site_id += 1
    return nodes, edges


def _boxes(nodes, edges):
    tb, lr = _busiest_sides(nodes, edges)
    return [
        (
            _visual_left(n.x, tb.get(n.device_id, 0)),
            _visual_top(n.y, lr.get(n.device_id, 0)),
            _visual_right(n.x, tb.get(n.device_id, 0)),
            _visual_bottom(n.y, lr.get(n.device_id, 0)),
            n,
        )
        for n in nodes
    ]


def _gap(a, b) -> float:
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3])


def test_the_whole_network_does_not_become_a_ribbon():
    """帯にしない。画面に収めたときに線も名前も読めなくなる。"""
    nodes, edges = _world()
    report = topology_world.arrange(nodes, edges)

    assert report.blocks > 4
    assert report.tries > 1, "候補を1つしか試していない"
    assert 1 / topology_world.MAX_RATIO <= report.ratio <= topology_world.MAX_RATIO
    assert report.violations == []


def test_no_two_boxes_end_up_close_enough_to_read_as_one():
    nodes, edges = _world()
    topology_world.arrange(nodes, edges)
    boxes = _boxes(nodes, edges)
    for a, b in itertools.combinations(boxes, 2):
        assert _gap(a, b) >= MIN_BOX_GAP, f"{a[4].hostname} と {b[4].hostname}"


def test_each_site_stays_in_one_piece():
    """拠点が混ざると、どこまでが1拠点なのかが図から読めなくなる。"""
    nodes, edges = _world()
    topology_world.arrange(nodes, edges)

    rects: dict[object, list[float]] = {}
    for left, top, right, bottom, node in _boxes(nodes, edges):
        current = rects.get(node.site_id)
        if current is None:
            rects[node.site_id] = [left, top, right, bottom]
            continue
        current[0] = min(current[0], left)
        current[1] = min(current[1], top)
        current[2] = max(current[2], right)
        current[3] = max(current[3], bottom)

    for (a_id, a), (b_id, b) in itertools.combinations(rects.items(), 2):
        apart = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
        assert apart, f"拠点 {a_id} と {b_id} の範囲が交ざっている"


def test_the_upper_sites_sit_above_the_ones_below_them():
    """上位が上。ここが崩れると、どちらが上位かを図で言えなくなる。"""
    nodes, edges = _world()
    topology_world.arrange(nodes, edges)

    def top_of(prefix: str) -> float:
        return min(n.y for n in nodes if n.hostname.startswith(prefix))

    assert top_of("core-rt") < top_of("br0-rt")
    assert top_of("br0-rt") < top_of("br0-of")


def test_the_same_ledger_always_lands_in_the_same_place():
    """開くたびに場所が変わる図は覚えられない。"""
    first, edges = _world()
    topology_world.arrange(first, edges)
    second, edges2 = _world()
    topology_world.arrange(second, edges2)

    assert [(n.hostname, n.x, n.y) for n in first] == [(n.hostname, n.x, n.y) for n in second]


def test_a_small_ledger_keeps_the_old_placement():
    """拠点が2つ3つなら、1つの木として組んだ方が結線の上下が素直に出る。"""
    nodes, _ = _world(branches=1, offices_each=1)
    assert topology_world.should_use(nodes) is False

    many, _ = _world()
    assert topology_world.should_use(many) is True


def test_it_answers_quickly_enough_to_open_a_screen():
    """繰り返して選ぶとはいえ、画面を開くたびに走る。"""
    import time

    nodes, edges = _world(branches=8, offices_each=8)
    start = time.perf_counter()
    topology_world.arrange(nodes, edges)
    assert time.perf_counter() - start < 2.0
