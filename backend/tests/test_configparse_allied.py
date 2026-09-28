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


def test_the_lldp_table_gives_the_lines_for_the_diagram(parsed):
    """`show lldp neighbors` の表。図の線はここから引く。"""
    pairs = [(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors]
    assert ("port1.0.1", "core-at-01", "port1.0.24") in pairs
    assert ("port1.0.2", "core-sw-01", "GigabitEthernet1/0/2") in pairs


#: 公式の資料の書き方（VLANs Feature Overview and Configuration Guide
#: C613-22001-00 REV N p.13〜14、Getting Started with AlliedWare Plus の既定の設定）
OFFICIAL_FORMS = """!
hostname awplus
!
vlan database
 vlan 10 name TEST
 vlan 20-22 state enable
 vlan 30
!
interface port1.0.1-1.0.4
 switchport
 switchport mode access
 switchport access vlan 10
!
interface port1.0.5
 switchport mode trunk
 switchport trunk allowed vlan add 10,20
!
interface vlan10
 ip address 192.168.10.1/24
!
end
"""


def _official():
    return configparse.parse(OFFICIAL_FORMS)


def test_vlans_made_without_a_name_are_still_vlans():
    """`vlan 20-22 state enable` を読まないと、使っている VLAN が未定義に見える。"""
    assert _official().vlans == {10: "TEST", 20: "", 21: "", 22: "", 30: ""}


def test_a_range_of_ports_is_opened_one_by_one():
    """`port1.0.1-1.0.4` を1つの口として持つと、間の口が台帳から消える。"""
    ports = {i.name: i for i in _official().interfaces}
    assert "port1.0.1-1.0.4" not in ports
    for number in range(1, 5):
        port = ports[f"port1.0.{number}"]
        assert port.mode == "access"
        assert port.vlan == 10


def test_the_ports_after_a_range_are_left_as_they_are():
    ports = {i.name: i for i in _official().interfaces}
    assert ports["port1.0.5"].trunk_vlans == [10, 20]
    assert ports["Vlan10"].ip_address == "192.168.10.1/24"
