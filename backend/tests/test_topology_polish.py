"""配置の整頓。ツリーを組み直さず、マス目・揃い・近すぎる重なりだけ触る。"""

from app.models.device import DeviceStatus, DeviceType
from app.models.topology import LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import (
    LAYOUT_GRID,
    TopologyEdgeView,
    TopologyNodeView,
    _box_height,
    _box_width,
    _busiest_side_links,
    _polish_layout,
    _shift_centers_to,
    _snap_to_grid,
    _visual_left,
    _visual_right,
)


def edge(
    edge_id: int,
    source: int,
    target: int,
    source_interface: str,
    target_interface: str = "GigabitEthernet0/0",
) -> TopologyEdgeView:
    return TopologyEdgeView(
        id=edge_id,
        source=source,
        target=target,
        source_interface=source_interface,
        target_interface=target_interface,
        origin=LinkOrigin.MANUAL,
        protocol=None,
        label=None,
        confirmed_both_ways=True,
    )


def node(
    device_id: int, hostname: str, x: float, y: float, *, outside: bool = False
) -> TopologyNodeView:
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
        outside=outside,
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
        node(2, "b", 240, 0),  # 300 まで 60。上限 80 以内
    ]
    _polish_layout(nodes)
    assert nodes[1].x - nodes[0].x >= 300


def test_a_column_is_not_flattened_into_a_row():
    """縦に並んだ列を1段に潰して横へ飛ばさない。"""
    nodes = [
        node(1, "a", 800, 0),
        node(2, "b", 800, 20),
        node(3, "c", 800, 40),
        node(4, "d", 800, 60),
        node(5, "e", 800, 80),
    ]
    _polish_layout(nodes)
    xs = {n.x for n in nodes}
    ys = {n.y for n in nodes}
    assert len(xs) == 1
    assert len(ys) >= 3
    assert max(n.x for n in nodes) - min(n.x for n in nodes) < 80


def test_stacked_nodes_are_separated_in_one_polish():
    """同じ座標に積み重なった機器は、一回で読める間隔まで離す。"""
    nodes = [
        node(1, "a", 0, 0),
        node(2, "b", 0, 0),
        node(3, "c", 0, 0),
    ]
    _polish_layout(nodes)
    xs = sorted(n.x for n in nodes)
    assert xs[1] - xs[0] >= 300
    assert xs[2] - xs[1] >= 300
    first = [(n.device_id, n.x, n.y) for n in nodes]
    _polish_layout(nodes)
    second = [(n.device_id, n.x, n.y) for n in nodes]
    assert first == second


def test_polish_reorders_a_column_to_match_port_order():
    """同じ列の相手を、ハブのポート順に入れ替えて交差を減らす。"""
    hub = node(1, "hub", 0, 200)
    # ポートは 1,2,3 なのに、縦の並びが逆になっている
    a = node(2, "a", 400, 400)
    b = node(3, "b", 400, 200)
    c = node(4, "c", 400, 0)
    nodes = [hub, a, b, c]
    edges = [
        edge(1, 1, 2, "GigabitEthernet0/1"),
        edge(2, 1, 3, "GigabitEthernet0/2"),
        edge(3, 1, 4, "GigabitEthernet0/3"),
    ]
    _polish_layout(nodes, edges=edges, owned={1, 2, 3, 4})
    by_id = {n.device_id: n for n in nodes}
    assert by_id[2].y < by_id[3].y < by_id[4].y
    assert len({n.x for n in (a, b, c)}) == 1


def test_wobbly_column_is_not_flattened_into_a_row():
    """手置きで列が数pxずれていても、1段に潰して横へ飛ばさない。"""
    nodes = [
        node(1, "a", 800, 0),
        node(2, "b", 808, 20),
        node(3, "c", 796, 40),
        node(4, "d", 804, 60),
        node(5, "e", 802, 80),
    ]
    _polish_layout(nodes)
    xs = {n.x for n in nodes}
    ys = {n.y for n in nodes}
    assert len(xs) == 1
    assert len(ys) >= 3
    assert max(n.x for n in nodes) - min(n.x for n in nodes) < 80


def test_parallel_links_do_not_stack_neighbors():
    """同じ相手への線が2本あっても、整頓で箱を重ねない。"""
    hub = node(1, "hub", 0, 200)
    a = node(2, "a", 400, 80)
    b = node(3, "b", 400, 320)
    nodes = [hub, a, b]
    edges = [
        edge(1, 1, 2, "GigabitEthernet0/1"),
        edge(2, 1, 2, "GigabitEthernet0/2"),
        edge(3, 1, 3, "GigabitEthernet0/3"),
    ]
    _polish_layout(nodes, edges=edges, owned={1, 2, 3})
    assert {a.x, a.y} != {b.x, b.y}


def test_uncross_does_not_pull_unaligned_neighbors():
    """同じ列にいない相手まで入れ替えて、揃えた行を崩さない。"""
    hub = node(1, "hub", 0, 200)
    row = node(2, "row", 400, 200)
    below = node(3, "below", 80, 480)
    nodes = [hub, row, below]
    edges = [
        edge(1, 1, 2, "GigabitEthernet0/1"),
        edge(2, 1, 3, "GigabitEthernet0/2"),
    ]
    _polish_layout(nodes, edges=edges, owned={1, 2, 3})
    assert abs(row.y - 200) <= LAYOUT_GRID * 2
    assert below.y >= 400


def test_busiest_matches_max_on_one_side_not_total():
    """箱の広がりは1辺の最大本数。総本数だと上下左右に分散した機器まで広がる。"""
    hub = node(1, "hub", 0, 0)
    # 上3・下3・右3 → 総9だが1辺は3なので既定のまま
    nodes = [hub] + [node(10 + i, f"u{i}", i * 40, -200) for i in range(3)]
    nodes += [node(20 + i, f"d{i}", i * 40, 200) for i in range(3)]
    nodes += [node(30 + i, f"r{i}", 500, i * 80) for i in range(3)]
    edges = []
    eid = 1
    for other in nodes[1:]:
        edges.append(edge(eid, 1, other.device_id, f"Gi0/{eid}"))
        eid += 1
    busiest = _busiest_side_links(nodes, edges)
    assert busiest[1] == 3
    assert _box_width(busiest[1]) == 190.0
    assert _box_height(3) == 78.0 + 22.0


def test_shift_centers_uses_default_half_not_grown_width():
    """広げた箱の中心は保存左上+既定幅/2。実幅/2だと広い箱へ寄る。"""
    narrow = node(1, "narrow", 0, 0)
    wide = node(2, "wide", 240, 0)
    _shift_centers_to([narrow, wide], 215.0)
    assert narrow.x == 0.0
    assert wide.x == 240.0


def test_polish_separates_wide_hub_from_neighbor():
    """上下に結線が多い箱が隣へ食い込むとき、整頓で見た目のすき間を空ける。"""
    hub = node(1, "hub", 0, 0)
    # 同じ段の隣。保存差 240 だと幅の広いハブと食い込む
    neighbor = node(2, "neighbor", 240, 0)
    # 下辺の本数を稼ぐ（幅が伸びる向き）
    leaves = [node(10 + i, f"leaf{i}", i * 40, 400) for i in range(7)]
    nodes = [hub, neighbor, *leaves]
    edges = [edge(100, 1, 2, "Gi0/0")] + [
        edge(101 + i, 1, leaf.device_id, f"Gi0/{i + 1}") for i, leaf in enumerate(leaves)
    ]
    # 下辺 7 + 右の隣 1 だが、下辺が 7 で幅が伸びる
    assert _busiest_side_links(nodes, edges)[1] == 7
    assert _visual_right(hub.x, 7) > _visual_left(neighbor.x, 0)
    _polish_layout(nodes, edges=edges, owned={n.device_id for n in nodes})
    assert _visual_left(neighbor.x, 0) >= _visual_right(hub.x, 7) - 1


def test_box_height_grows_when_left_right_is_busy():
    """左右の辺に多いときは高さを伸ばす。幅を広げても縦の被りは消えない。"""
    assert _box_height(2) == 78.0
    assert _box_height(3) == 78.0 + 22.0
    assert _box_height(8) == 78.0 + 6 * 22.0
    assert _box_width(8) == 190.0 + 5 * 55.0
