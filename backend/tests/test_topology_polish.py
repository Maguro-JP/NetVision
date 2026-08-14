"""配置の整頓。ツリーを組み直さず、マス目・揃い・近すぎる重なりだけ触る。"""

from app.models.device import DeviceStatus, DeviceType
from app.services.lifecycle import Severity
from app.services.topology import (
    LAYOUT_GRID,
    TopologyNodeView,
    _polish_layout,
    _snap_to_grid,
)


def node(device_id: int, hostname: str, x: float, y: float) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=hostname,
        vendor="Cisco",
        model="X",
        device_type=str(DeviceType.SWITCH),
        status=str(DeviceStatus.ACTIVE),
        site_id=1,
        site_name="本社",
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=0,
        x=x,
        y=y,
        placed=True,
    )


def test_near_rows_are_aligned_to_the_grid():
    """少しだけ段がずれている機器は、同じ段に揃えてマス目に乗せる。"""
    nodes = [
        node(1, "a", 0, 12),
        node(2, "b", 300, 28),
        node(3, "c", 600, 8),
    ]
    _polish_layout(nodes)
    assert len({n.y for n in nodes}) == 1
    assert nodes[0].y == _snap_to_grid(nodes[0].y)
    assert all(n.x == _snap_to_grid(n.x) for n in nodes)


def test_polish_does_not_rebuild_into_a_tree():
    """人が離して置いた機器を、初期のツリー配置へ飛ばさない。"""
    nodes = [
        node(1, "left", 40, 40),
        node(2, "right", 800, 40),
        node(3, "below", 40, 400),
    ]
    _polish_layout(nodes)
    by_id = {n.device_id: (n.x, n.y) for n in nodes}
    assert by_id[2][0] >= 720
    assert by_id[3][1] >= 320
    assert all(n.x % LAYOUT_GRID == 0 and n.y % LAYOUT_GRID == 0 for n in nodes)


def test_polish_stays_near_the_manual_position():
    """整頓でマス目2つ以上飛ばない。"""
    nodes = [node(1, "a", 103, 97), node(2, "b", 108, 410)]
    _polish_layout(nodes)
    assert abs(nodes[0].x - 103) <= LAYOUT_GRID * 2 + 1
    assert abs(nodes[0].y - 97) <= LAYOUT_GRID * 2 + 1
    assert abs(nodes[1].y - 410) <= LAYOUT_GRID * 2 + 1


def test_max_move_does_not_split_an_aligned_row():
    """横に空けても、揃えた段を縮めて別の段へ落とさない。"""
    nodes = [
        node(1, "a", 0, 0),
        node(2, "b", 15, 18),
        node(3, "c", 30, 34),
    ]
    _polish_layout(nodes)
    assert len({n.y for n in nodes}) == 1


def test_polish_is_idempotent():
    """同じ図で二度押しても、位置が変わらない。"""
    nodes = [
        node(1, "a", 100, 100),
        node(2, "b", 120, 105),
        node(3, "c", 140, 98),
    ]
    _polish_layout(nodes)
    first = [(n.device_id, n.x, n.y) for n in nodes]
    _polish_layout(nodes)
    second = [(n.device_id, n.x, n.y) for n in nodes]
    assert first == second


def test_polish_does_not_creep_apart_on_repeated_passes():
    """足りない間隔を、押すたびに少しずつ広げていかない。"""
    nodes = [
        node(1, "a", 100, 100),
        node(2, "b", 120, 100),
        node(3, "c", 140, 100),
    ]
    positions = []
    for _ in range(5):
        _polish_layout(nodes)
        positions.append([(n.device_id, n.x, n.y) for n in nodes])
    assert positions[0] == positions[-1]


def test_near_overlap_within_budget_is_cleared():
    """上限内で空けられる近さなら、箱が重ならない間隔まで空ける。"""
    nodes = [
        node(1, "a", 0, 0),
        node(2, "b", 180, 0),  # 230 まで 50。上限 80 以内
    ]
    _polish_layout(nodes)
    assert nodes[1].x - nodes[0].x >= 230
