"""アライドテレシス AlliedWare Plus の config を読む。

IOS に近いが、ポート名と、アドレス・経路の書き方、VLAN の名前の置き場所が
違う。そこを取り違えると、名前もアドレスも入らないまま図に出てしまう。
"""

from pathlib import Path

import pytest

from app.services import configparse
from app.services.configparse import Vendor

FIXTURE = Path(__file__).parent / "fixtures" / "allied-x530-01.txt"


@pytest.fixture()
def parsed():
    return configparse.parse(FIXTURE.read_text(encoding="utf-8"))


def test_it_is_recognised_as_allied(parsed):
    assert parsed.vendor is Vendor.ALLIED
    assert parsed.os_name == "AlliedWare Plus"
    assert parsed.os_version == "5.5.2-2.1"
    assert parsed.model == "AT-x530-28GTXm"
    assert parsed.serial_number == "A11223344"
    assert parsed.hostname == "at-x530-01"


def test_the_vlan_names_come_from_the_vlan_database(parsed):
    assert parsed.vlans[10] == "USERS"
    assert parsed.vlans[20] == "SERVERS"
    assert parsed.vlans[100] == "GUEST"


def test_the_ports_are_read(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["port1.0.10"].vlan == 20
    assert ports["port1.0.10"].mode == "access"
    assert ports["port1.0.24"].enabled is False
    assert ports["port1.0.1"].mode == "trunk"
    assert ports["port1.0.1"].trunk_vlans == [10, 20]
    assert ports["port1.0.1"].native_vlan == 1


def test_addresses_are_read_with_their_length(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["Vlan10"].ip_address == "10.0.10.1/24"
    assert ports["Vlan20"].ip_address == "10.0.20.1/24"


def test_routes_are_read_with_their_length(parsed):
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "10.0.10.254"
    assert routes["10.9.0.0/16"].distance == 200


def test_ospf_on_a_port_is_read(parsed):
    port = {i.name: i for i in parsed.interfaces}["Vlan10"]
    assert port.ospf_area == "0.0.0.0"


def test_hardware_acls_are_read_and_their_port_is_known(parsed):
    """番号付きは IOS の読み手で、名前付き（access-list hardware）はこちらで読む。

    当てるのは `access-group 名前` で向きを書かない。入ってくる側に効く。
    """
    acls = {a.name: a for a in parsed.acls}
    assert [e.action for e in acls["3001"].entries] == ["permit", "deny"]
    assert acls["3001"].applied_to == ["interface port1.0.24 入り"]
    assert [e.seq for e in acls["GUEST-IN"].entries] == ["10", "20"]
    assert acls["GUEST-IN"].entries[0].destination_port == "eq 53"
    assert acls["GUEST-IN"].applied_to == ["interface port1.0.24 入り"]
