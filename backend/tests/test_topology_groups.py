"""キャリア網の段と、機器を囲む枠。"""

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link, LinkOrigin
from app.services.lifecycle import Severity
from app.services.topology import (
    TopologyEdgeView,
    TopologyGroupView,
    TopologyNodeView,
    _busiest_sides,
    _pull_groups_together,
    _visual_left,
    _visual_right,
    apply_planes,
    attach_groups,
    get_graph,
    plane_of,
    scope_graph,
)
from tests.conftest import API


def test_plane_of_splits_core_metro_access():
    assert plane_of(0, 3) == "backbone"
    assert plane_of(2, 3) == "metro"
    assert plane_of(3, 3) == "access"
    assert plane_of(0, 1) == "backbone"
    assert plane_of(1, 1) == "access"


def test_apply_planes_marks_the_edge_as_access():
    nodes = [
        TopologyNodeView(
            device_id=i,
            hostname=f"n{i}",
            vendor="x",
            model="x",
            device_type="router",
            status="active",
            site_id=None,
            site_name=None,
            mgmt_ip=None,
            severity=Severity.OK,
            interface_count=0,
            x=0,
            y=0,
            placed=False,
        )
        for i in (1, 2, 3)
    ]
    apply_planes(nodes, {1: 0, 2: 1, 3: 2})
    assert [n.plane for n in nodes] == ["backbone", "metro", "access"]


def test_group_encloses_selected_devices(db_session, client):
    site = Site(name="関西支社")
    db_session.add(site)
    db_session.flush()
    devices = []
    for name in ("ksi-br-rt-01", "ksi-br-sw-01"):
        device = Device(
            hostname=name,
            vendor="Cisco",
            model="X",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(device)
        devices.append(device)
    db_session.flush()
    db_session.add(
        Link(
            a_device_id=devices[0].id,
            a_interface="Gi0/1",
            b_device_id=devices[1].id,
            b_interface="Gi1/0/1",
        )
    )
    db_session.commit()

    created = client.post(
        f"{API}/topology/groups",
        json={
            "layer": "physical",
            "name": "関西支社",
            "device_ids": [devices[0].id, devices[1].id],
            "plane": "metro",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "関西支社"
    assert body["plane"] == "metro"
    assert set(body["device_ids"]) == {devices[0].id, devices[1].id}

    graph = attach_groups(db_session, scope_graph(get_graph(db_session), site_id=site.id))
    assert len(graph.groups) == 1
    assert graph.groups[0].name == "関西支社"
    assert {n.plane for n in graph.nodes}

    painted = client.patch(
        f"{API}/topology/groups/{body['id']}",
        json={"color": "#aa3344"},
    )
    assert painted.status_code == 200
    assert painted.json()["color"] == "#aa3344"

    gone = client.delete(f"{API}/topology/groups/{body['id']}")
    assert gone.status_code == 204


def test_groups_from_sites_skips_devices_already_in_a_frame(db_session, client):
    site = Site(name="大阪営業所")
    db_session.add(site)
    db_session.flush()
    devices = []
    for name in ("osk-sw-01", "osk-sw-02"):
        device = Device(
            hostname=name,
            vendor="Cisco",
            model="X",
            device_type=DeviceType.SWITCH,
            site_id=site.id,
        )
        db_session.add(device)
        devices.append(device)
    db_session.commit()

    first = client.post(
        f"{API}/topology/groups/from-sites",
        params={"layer": "physical", "device_ids": f"{devices[0].id},{devices[1].id}"},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()) == 1
    assert first.json()[0]["name"] == "大阪営業所"

    again = client.post(
        f"{API}/topology/groups/from-sites",
        params={"layer": "physical", "device_ids": f"{devices[0].id},{devices[1].id}"},
    )
    assert again.status_code == 200
    assert again.json() == []


def test_groups_from_planes_follows_carrier_ranks(db_session, client):
    site = Site(name="本社")
    db_session.add(site)
    db_session.flush()
    core = Device(
        hostname="hq-core-01",
        vendor="Cisco",
        model="X",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    edge = Device(
        hostname="hq-acc-01",
        vendor="Cisco",
        model="X",
        device_type=DeviceType.SWITCH,
        site_id=site.id,
    )
    db_session.add_all([core, edge])
    db_session.flush()
    db_session.add(
        Link(
            a_device_id=core.id,
            a_interface="Gi0/1",
            b_device_id=edge.id,
            b_interface="Gi1/0/1",
        )
    )
    db_session.commit()

    created = client.post(
        f"{API}/topology/groups/from-planes",
        params={"layer": "physical", "device_ids": f"{core.id},{edge.id}"},
    )
    assert created.status_code == 200, created.text
    names = {item["name"] for item in created.json()}
    assert names
    assert names <= {"コア網", "メトロ／集約", "アクセス"}
    assert {item["plane"] for item in created.json()} <= {"backbone", "metro", "access"}

    svg = client.get(f"{API}/topology/export.svg")
    assert svg.status_code == 200
    body = svg.text
    assert any(name in body for name in names)
    assert "stroke-dasharray" in body


def _row_node(device_id: int, x: float, *, placed: bool = False) -> TopologyNodeView:
    return TopologyNodeView(
        device_id=device_id,
        hostname=f"sw-{device_id:02d}",
        vendor="x",
        model="x",
        device_type="switch",
        status="active",
        site_id=None,
        site_name=None,
        mgmt_ip=None,
        severity=Severity.OK,
        interface_count=0,
        x=x,
        y=400,
        placed=placed,
    )


def test_group_members_end_up_next_to_each_other():
    """枠のメンバーの間に、枠の外の機器が挟まらないこと。

    挟まると枠がそこまで伸びて、囲んだ意味が無くなる。
    """
    row = [_row_node(device_id, index * 240.0) for index, device_id in enumerate([1, 2, 3, 4])]
    group = TopologyGroupView(id=1, name="拠点A", plane="custom", color="#888", device_ids=[1, 3])

    _pull_groups_together(row, [group])

    order = [node.device_id for node in sorted(row, key=lambda node: node.x)]
    assert order.index(3) - order.index(1) == 1
    # 使う場所そのものは変えない（間隔や重なりは配置側で決まっている）
    assert sorted(node.x for node in row) == [0.0, 240.0, 480.0, 720.0]


def test_a_row_the_user_placed_is_left_alone():
    """人が置いた段は触らない。並べ替えるとその意思を壊す。"""
    row = [
        _row_node(1, 0.0),
        _row_node(2, 240.0, placed=True),
        _row_node(3, 480.0),
        _row_node(4, 720.0),
    ]
    group = TopologyGroupView(id=1, name="拠点A", plane="custom", color="#888", device_ids=[1, 3])

    _pull_groups_together(row, [group])

    assert [node.x for node in row] == [0.0, 240.0, 480.0, 720.0]


def test_reordering_does_not_make_wide_boxes_overlap():
    """結線が多い機器は箱が広い。並べ替えたあとも食い込まないこと。"""
    row = [_row_node(device_id, index * 300.0) for index, device_id in enumerate([1, 2, 3, 4])]
    # 4番の機器に上下の辺の結線を集め、箱を広げる
    edges = [
        TopologyEdgeView(
            id=index,
            source=4,
            target=1,
            source_interface=f"GigabitEthernet0/{index}",
            target_interface="GigabitEthernet0/0",
            origin=LinkOrigin.MANUAL,
            protocol=None,
            label=None,
            confirmed_both_ways=True,
            source_side="bottom",
            target_side="top",
        )
        for index in range(1, 9)
    ]
    group = TopologyGroupView(id=1, name="拠点A", plane="custom", color="#888", device_ids=[1, 4])

    _pull_groups_together(row, [group], edges)

    busiest_tb, _ = _busiest_sides(row, edges)
    ordered = sorted(row, key=lambda node: node.x)
    for left, right in zip(ordered, ordered[1:], strict=False):
        assert _visual_right(left.x, busiest_tb.get(left.device_id, 0)) <= _visual_left(
            right.x, busiest_tb.get(right.device_id, 0)
        )
