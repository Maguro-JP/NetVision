"""HPE / Aruba の AOS-S（ProCurve）を読む。

同じ Aruba でも AOS-CX とは別物で、VLAN の下に口を並べる。向きが IOS と
逆なので、IOS の目で読むと口が1つも取れない。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

AOS_S = """; JL253A Configuration Editor; Created on release #YA.16.10.0013
hostname "sw-aoss-01"
module 1 type jl253a
trunk 23-24 trk1 lacp
ip default-gateway 10.0.0.1
vlan 1
   name "DEFAULT_VLAN"
   no untagged 1-24
   no ip address
   exit
vlan 10
   name "USERS"
   untagged 1-20
   tagged Trk1
   ip address 10.0.0.5 255.255.255.0
   exit
vlan 30
   name "MGMT"
   tagged Trk1
   exit
vlan 999
   name "NATIVE"
   untagged Trk1
   exit
interface 1
   name "shitsumu"
   exit
interface 21
   name "yobi"
   disable
   exit
ip route 192.168.0.0 255.255.0.0 10.0.0.254
"""


def _parsed():
    return configparse.parse(AOS_S)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_it_is_told_apart_from_aruba_cx() -> None:
    """同じメーカーでも書き方が別物なので、取り違えると口が読めない。"""
    assert _parsed().vendor is Vendor.ARUBA_OS


def test_the_name_and_the_model_and_the_version_are_read() -> None:
    parsed = _parsed()
    assert parsed.hostname == "sw-aoss-01"
    assert parsed.model == "jl253a"
    assert parsed.os_version == "YA.16.10.0013"


def test_the_vlans_and_their_names_are_read() -> None:
    assert _parsed().vlans == {
        1: "DEFAULT_VLAN",
        10: "USERS",
        30: "MGMT",
        999: "NATIVE",
    }


def test_a_port_written_only_under_a_vlan_still_becomes_a_port() -> None:
    """口の下に VLAN を書く IOS と向きが逆。組み直さないと口が取れない。"""
    port = _port("Port1")
    assert port.mode == "access"
    assert port.vlan == 10


def test_the_untagged_vlan_on_a_tagged_port_is_the_native_vlan() -> None:
    """タグ無しの食い違いは、分けたはずのものが混ざる、いちばん重い形。"""
    port = _port("Trk1")
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10, 30]
    assert port.native_vlan == 999


def test_a_port_that_is_written_as_disabled_is_not_counted_as_live() -> None:
    """AOS-S は既定で上がっている。落としてあるときだけ書く。"""
    assert _port("Port21").enabled is False
    assert _port("Port1").enabled is True


def test_the_name_written_on_a_port_is_kept() -> None:
    assert _port("Port1").description == "shitsumu"


def test_the_address_on_a_vlan_is_read() -> None:
    assert _port("Vlan10").ip_address == "10.0.0.5/24"


def test_bundled_ports_are_read() -> None:
    assert _port("Port23").lag_group == 1
    assert _port("Port24").lag_mode == "active"


def test_the_default_route_written_as_a_gateway_is_still_a_route() -> None:
    """`ip default-gateway` は語が違うだけ。別扱いにすると経路が抜ける。"""
    routes = {route.destination: route.next_hop for route in _parsed().routes}
    assert routes["0.0.0.0/0"] == "10.0.0.1"
    assert routes["192.168.0.0/16"] == "10.0.0.254"


def test_a_cisco_config_is_not_taken_over() -> None:
    """`ip default-gateway` は Cisco にもある。それを目印にすると横取りする。"""
    cisco = (
        "hostname sw-01\n!\nvlan 10\n name USERS\n!\n"
        "interface GigabitEthernet0/1\n switchport access vlan 10\n!\n"
        "ip default-gateway 10.0.0.10\n"
    )
    assert configparse.parse(cisco).vendor is Vendor.CISCO_IOS


def test_the_commands_to_get_there_can_be_written() -> None:
    """作れても投入コマンドにならないなら、途中までしか使えない。"""
    from app.services.configdiff import build

    current = 'hostname "sw-01"\nvlan 10\n   name "USERS"\n   untagged 1-20\n   exit\n'
    future = current.replace("   exit\n", "   tagged 24\n   exit\n")
    diff = build(current, future, vendor=Vendor.ARUBA_OS)
    assert diff.verified
    assert "tagged 24" in diff.script
