"""図の外にいる相手（対向）の置き方。

対向を内側と同じ間隔で並べると、小さい箱が縦に大きく散らばって、どれが
図の外なのか読み取れない。上下に詰めて1つのまとまりにする。
横へは寄せない。拠点の中の機器と地続きに見えると、どこまでが自分の拠点なのかが
分からなくなるため。
"""

from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import (
    _OUTSIDE_HEIGHT,
    TopologyEdgeView,
    TopologyNodeView,
    _stack_outside,
)


def _node(device_id: int, x: float, y: float, *, outside: bool = False) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=f"dev-{device_id:02d}",
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
        placed=False,
        outside=outside,
    )


def _edge(edge_id: int, source: int, target: int) -> TopologyEdgeView:
    return TopologyEdgeView(
        id=edge_id,
        source=source,
        target=target,
        source_interface="Gi0/1",
        target_interface="Gi0/1",
        origin=LinkOrigin.DISCOVERED,
        protocol="lldp",
        label=None,
        confirmed_both_ways=True,
    )


def test_outside_peers_are_stacked_close_together():
    inside = _node(1, 0.0, 0.0)
    far = [
        _node(2, 400.0, -300.0, outside=True),
        _node(3, 400.0, 300.0, outside=True),
    ]
    edges = [_edge(1, 1, 2), _edge(2, 1, 3)]
    before_x = [node.x for node in far]

    _stack_outside([inside, *far], edges)

    assert [node.x for node in far] == before_x, "横へ寄せてはいけない"
    gap = abs(far[1].y - far[0].y)
    assert gap < 300, "上下に詰まっていない"
    assert gap >= _OUTSIDE_HEIGHT, "箱が重なるほど詰めてはいけない"


def test_the_order_from_top_to_bottom_is_kept():
    """上下の順番は変えない。上位が上、配下が下という関係が読めなくなる。"""
    inside = _node(1, 0.0, 0.0)
    upper = _node(2, 400.0, -400.0, outside=True)
    lower = _node(3, 400.0, 400.0, outside=True)

    _stack_outside([inside, upper, lower], [_edge(1, 1, 2), _edge(2, 1, 3)])

    assert upper.y < lower.y


def test_a_device_placed_by_hand_is_left_alone():
    """人が置いた機器は動かさない。置き直した意味が無くなる。"""
    inside = _node(1, 0.0, 0.0)
    fixed = _node(2, 400.0, -400.0, outside=True)
    fixed.placed = True
    moving = _node(3, 400.0, 400.0, outside=True)

    _stack_outside([inside, fixed, moving], [_edge(1, 1, 2), _edge(2, 1, 3)])

    assert fixed.y == -400.0
