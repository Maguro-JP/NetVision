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


def test_the_lldp_table_gives_the_links_and_skips_the_unnamed():
    """`show lldp neighbors` の表から隣を読む。名乗っていない相手は作らない。

    出典: Dell SmartFabric OS10 User Guide「show lldp neighbors」の出力例
    （`Loc PortID / Rem Host Name / Rem Port Id / Rem Chassis Id`、
    名乗らない相手は `Not Advertised`）。
    """
    text = "\n".join(
        [
            "OS10# show version",
            "Dell EMC Networking OS10 Enterprise",
            "OS Version: 10.5.6.2",
            "OS10# show lldp neighbors",
            "Loc PortID        Rem Host Name   Rem Port Id         Rem Chassis Id",
            "----------------------------------------------------------------------",
            "ethernet1/1/2     Not Advertised  fortyGigE 0/56      00:01:e8:8a:fd:35",
            "ethernet1/1/53    S5248F-2        ethernet1/1/54      54:bf:64:ba:33:c0",
            "",
        ]
    )
    parsed = configparse.parse(text)
    found = {(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors}
    # 口の名前は、読み手の中でそろえた形（先頭が大文字）になる
    assert ("Ethernet1/1/53", "S5248F-2", "Ethernet1/1/54") in found
    assert not any(n.remote_hostname == "Not Advertised" for n in parsed.neighbors)


def test_ports_written_as_an_interface_range_are_opened_one_by_one():
    """`interface range ethernet 1/1/1-1/1/4` に書いた設定は、4つの口に入る。

    開かないと、その設定がどの口にも入らず、口自体も台帳から落ちる。

    出典: Dell SmartFabric OS10 User Guide「interface range」
    （`OS10(config)# interface range ethernet 1/1/7-1/1/24`、
    カンマ区切りで最大6範囲）。
    """
    text = "\n".join(
        [
            "OS10# show version",
            "Dell EMC Networking OS10 Enterprise",
            "OS Version: 10.5.6.2",
            "!",
            "interface range ethernet 1/1/1-1/1/2,1/1/5",
            " description office",
            " no shutdown",
            " switchport access vlan 10",
            "!",
            "interface ethernet 1/1/24",
            " no shutdown",
            "!",
        ]
    )
    parsed = configparse.parse(text)
    ports = {i.name: i for i in parsed.interfaces}
    assert sorted(ports) == [
        "Ethernet1/1/1",
        "Ethernet1/1/2",
        "Ethernet1/1/24",
        "Ethernet1/1/5",
    ]
    assert all(ports[f"Ethernet1/1/{n}"].vlan == 10 for n in (1, 2, 5))
    assert all(ports[f"Ethernet1/1/{n}"].description == "office" for n in (1, 2, 5))
    assert ports["Ethernet1/1/24"].vlan is None
