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


STACKED = """; JL322A Configuration Editor; Created on release #WC.16.10.0013
hostname "sw-aoss-02"
trunk 1/49-1/50 trk1 lacp
vlan 1
   name "DEFAULT_VLAN"
   exit
vlan 40
   name "OFFICE"
   untagged 1/1-1/4,A1-A3
   exit
interface Trk1
   tagged vlan 20-22,25
   untagged vlan 30
   exit
interface 1/5
   untagged vlan 40
   exit
"""


def _stacked(name: str):
    parsed = configparse.parse(STACKED)
    return next(port for port in parsed.interfaces if port.name == name)


def test_a_range_on_a_stack_member_is_opened() -> None:
    """`1/1-1/4` を1つの口として持つと、間の口が台帳から消える。"""
    for name in ("1/1", "1/2", "1/3", "1/4", "A1", "A2", "A3"):
        assert _stacked(name).vlan == 40


def test_bundled_ports_on_a_stack_member_are_read() -> None:
    assert _stacked("1/49").lag_group == 1
    assert _stacked("1/50").lag_group == 1


def test_a_range_with_a_different_head_is_left_as_written() -> None:
    """`1/49-2/1` は機器の中の並びでしか開けない。推し量って開かない。"""
    from app.services.configparse.arubaos import _ports

    assert _ports("1/49-2/1") == ["1/49-2/1"]


def test_vlans_written_under_a_port_are_read() -> None:
    """口の側に書いても VLAN の下に書いても同じ意味。片方しか読まないと抜ける。"""
    trunk = _stacked("Trk1")
    assert trunk.mode == "trunk"
    assert trunk.trunk_vlans == [20, 21, 22, 25]
    assert trunk.native_vlan == 30
    access = _stacked("1/5")
    assert access.mode == "access"
    assert access.vlan == 40
