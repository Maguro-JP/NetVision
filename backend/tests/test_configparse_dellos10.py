"""Dell OS10 を読む。

見た目が IOS に近いので、そのまま IOS として読むと通ってしまう。
だが口の名前も住所の書き方も違うので、通ったところで中身が空になる。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

OS10 = """! Model: S4128F-ON
hostname sw-dell-01
!
interface vlan 30
 ip address 10.0.0.5/24
 no shutdown
!
interface ethernet 1/1/1
 description shitsumu
 switchport mode access
 switchport access vlan 10
 no shutdown
!
interface ethernet 1/1/24
 description to core
 switchport mode trunk
 switchport trunk allowed vlan 10,30
 switchport access vlan 999
 no shutdown
!
interface ethernet 1/1/21
 shutdown
!
interface port-channel 1
 switchport mode trunk
!
interface ethernet 1/1/23
 channel-group 1 mode active
!
"""


def _parsed():
    return configparse.parse(OS10)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_it_is_told_apart_from_ios() -> None:
    parsed = _parsed()
    assert parsed.vendor is Vendor.DELL_OS10
    assert parsed.model == "S4128F-ON"


def test_a_port_written_with_a_space_is_still_one_port() -> None:
    """`ethernet 1/1/1` は種別と番号を空けて書く。"""
    assert _port("Ethernet1/1/1").vlan == 10


def test_the_address_written_with_a_length_is_read() -> None:
    assert _port("Vlan30").ip_address == "10.0.0.5/24"


def test_the_access_vlan_on_a_trunk_port_is_the_native_vlan() -> None:
    """同じ語でも、トランクの口に書いてあればタグ無しで渡す VLAN の意味。"""
    port = _port("Ethernet1/1/24")
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10, 30]
    assert port.native_vlan == 999
    assert port.vlan is None


def test_a_port_that_is_shut_is_read_as_shut() -> None:
    assert _port("Ethernet1/1/21").enabled is False


def test_bundled_ports_are_read() -> None:
    assert _port("Ethernet1/1/23").lag_group == 1
    assert _port("Ethernet1/1/23").lag_mode == "active"


def test_an_ios_config_is_not_taken_over() -> None:
    ios = "hostname sw-01\n!\ninterface GigabitEthernet0/1\n switchport access vlan 10\n!\n"
    assert configparse.parse(ios).vendor is Vendor.CISCO_IOS


def test_the_commands_to_get_there_can_be_written() -> None:
    from app.services.configdiff import build

    future = OS10.replace(
        " switchport trunk allowed vlan 10,30", " switchport trunk allowed vlan 10,30,40"
    )
    diff = build(OS10, future, vendor=Vendor.DELL_OS10)
    assert diff.verified
    assert "switchport trunk allowed vlan 10,30,40" in diff.script
