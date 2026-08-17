"""レイヤ切替の中身。

物理 / L2 / L3 / 論理は、同じ結線の別配置ではなく、描く内容が違う。
看板倒れ（選択肢だけあって中身が同じ）に戻さないこと。
"""

from app.models.device import Device, DeviceType
from app.models.topology import Interface, Link, LinkOrigin
from app.models.tunnel import DeviceTunnel
from app.services import topology
from app.services.configparse import normalize


def _device(db, hostname: str, device_type: DeviceType, mgmt_ip: str | None = None) -> Device:
    device = Device(
        hostname=hostname,
        vendor="Cisco",
        model="X",
        device_type=device_type,
        mgmt_ip=mgmt_ip,
    )
    db.add(device)
    db.flush()
    return device


def _link(db, a: Device, a_if: str, b: Device, b_if: str) -> Link:
    link = Link(
        a_device_id=a.id,
        a_interface=a_if,
        b_device_id=b.id,
        b_interface=b_if,
        origin=LinkOrigin.DISCOVERED,
        protocol="lldp",
        confirmed_both_ways=True,
    )
    db.add(link)
    db.flush()
    return link


def _port(db, device: Device, name: str, **kwargs) -> Interface:
    port = Interface(
        device_id=device.id,
        name=name,
        normalized_name=normalize(name),
        **kwargs,
    )
    db.add(port)
    db.flush()
    return port


def test_l2_keeps_vlan_links_and_drops_plain_wan(db_session):
    """L2 は VLAN が載っている線だけ。ルーター同士の素の WAN は出さない。"""
    core = _device(db_session, "core-sw", DeviceType.SWITCH)
    acc = _device(db_session, "acc-sw", DeviceType.SWITCH)
    edge = _device(db_session, "edge-rt", DeviceType.ROUTER)
    branch = _device(db_session, "br-rt", DeviceType.ROUTER)

    _link(db_session, core, "Gi1/0/1", acc, "Gi0/1")
    _link(db_session, edge, "Gi0/0", branch, "Gi0/0")
    _port(db_session, core, "Gi1/0/1", mode="trunk", native_vlan=999, trunk_vlans="10,20")
    _port(db_session, acc, "Gi0/1", mode="trunk", native_vlan=999, trunk_vlans="10,20")
    # WAN 側に VLAN は付けない
    _port(db_session, edge, "Gi0/0")
    _port(db_session, branch, "Gi0/0")
    db_session.commit()

    physical = topology.get_graph(db_session, "physical")
    l2 = topology.get_graph(db_session, "l2")

    assert len(physical.edges) == 2
    assert len(l2.edges) == 1
    assert {n.hostname for n in l2.nodes} == {"core-sw", "acc-sw"}
    ends = {l2.edges[0].source, l2.edges[0].target}
    assert ends == {core.id, acc.id}


def test_l2_keeps_manual_links_even_without_vlan(db_session):
    """手で引いた線は、VLAN が無くても L2 から消えない。"""
    core = _device(db_session, "core-sw", DeviceType.SWITCH)
    acc = _device(db_session, "acc-sw", DeviceType.SWITCH)
    link = Link(
        a_device_id=core.id,
        a_interface="",
        b_device_id=acc.id,
        b_interface="",
        origin=LinkOrigin.MANUAL,
    )
    db_session.add(link)
    db_session.commit()

    l2 = topology.get_graph(db_session, "l2")
    assert len(l2.edges) == 1
    assert l2.edges[0].origin is LinkOrigin.MANUAL
    assert {n.hostname for n in l2.nodes} == {"core-sw", "acc-sw"}


def test_l3_keeps_manual_links_even_without_ip(db_session):
    """手で引いた線は、IP が無くても L3 から消えない。"""
    left = _device(db_session, "sw-a", DeviceType.SWITCH)
    right = _device(db_session, "sw-b", DeviceType.SWITCH)
    db_session.add(
        Link(
            a_device_id=left.id,
            a_interface="",
            b_device_id=right.id,
            b_interface="",
            origin=LinkOrigin.MANUAL,
        )
    )
    db_session.commit()

    l3 = topology.get_graph(db_session, "l3")
    assert len(l3.edges) == 1
    assert l3.edges[0].origin is LinkOrigin.MANUAL


def test_l3_keeps_router_links_and_drops_switch_trunks(db_session):
    """L3 はルーター同士（や IP 付き）。スイッチ間トランクは L2 側。"""
    edge = _device(db_session, "edge-rt", DeviceType.ROUTER)
    fw = _device(db_session, "fw-01", DeviceType.FIREWALL)
    core = _device(db_session, "core-sw", DeviceType.SWITCH)
    acc = _device(db_session, "acc-sw", DeviceType.SWITCH)

    _link(db_session, edge, "Gi0/0", fw, "port1")
    _link(db_session, core, "Gi1/0/1", acc, "Gi0/1")
    _port(db_session, edge, "Gi0/0", ip_address="10.255.0.1/30")
    _port(db_session, fw, "port1", ip_address="10.255.0.2/30")
    _port(db_session, core, "Gi1/0/1", mode="trunk", trunk_vlans="10")
    _port(db_session, acc, "Gi0/1", mode="trunk", trunk_vlans="10")
    db_session.commit()

    l3 = topology.get_graph(db_session, "l3")
    l2 = topology.get_graph(db_session, "l2")

    assert {n.hostname for n in l3.nodes} == {"edge-rt", "fw-01"}
    assert {n.hostname for n in l2.nodes} == {"core-sw", "acc-sw"}


def test_logical_differs_from_physical_via_tunnels(db_session):
    """論理はトンネル直結。物理の経由とは別の線になる。"""
    center = _device(db_session, "center-rt", DeviceType.ROUTER, mgmt_ip="10.1.0.1")
    branch = _device(db_session, "br-rt", DeviceType.ROUTER, mgmt_ip="10.10.0.1")
    station = _device(db_session, "osaka-rt", DeviceType.ROUTER, mgmt_ip="10.101.1.1")

    _link(db_session, station, "Gi0/0", branch, "Gi0/1")
    _link(db_session, branch, "Gi0/0", center, "Gi0/0")
    db_session.add(
        DeviceTunnel(
            device_id=station.id,
            name="Tunnel0",
            kind="ipsec",
            destination="10.1.0.1",
            description="センター向け",
        )
    )
    db_session.commit()

    physical = topology.get_graph(db_session, "physical")
    logical = topology.get_graph(db_session, "logical")

    assert len(physical.edges) == 2
    assert len(logical.edges) == 1
    assert {logical.edges[0].source, logical.edges[0].target} == {center.id, station.id}
    assert "br-rt" not in {n.hostname for n in logical.nodes}


def test_logical_survives_duplicate_mgmt_ips_by_preferring_later_devices(db_session):
    """同じ管理IPが二台にあるとき、先勝ちでトンネル先を間違えないこと。"""
    fw = _device(db_session, "fw-01", DeviceType.FIREWALL, mgmt_ip="10.1.0.1")
    center = _device(db_session, "east-core-rt-01", DeviceType.ROUTER, mgmt_ip="10.1.0.1")
    station = _device(db_session, "osaka-rt-01", DeviceType.ROUTER, mgmt_ip="10.101.1.1")
    db_session.add(
        DeviceTunnel(
            device_id=station.id,
            name="Tunnel0",
            kind="ipsec",
            destination="10.1.0.1",
            description="センター向け",
        )
    )
    db_session.commit()

    graph = topology.get_graph(db_session, "logical")
    assert len(graph.edges) == 1
    assert {graph.edges[0].source, graph.edges[0].target} == {center.id, station.id}
    assert fw.id not in {graph.edges[0].source, graph.edges[0].target}
