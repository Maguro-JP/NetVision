"""VyOS / EdgeOS を読む。

Junos も set で書くので、そこだけで決めると取り違える。取り違えると
メーカーが変わり、保守期限も脆弱性も別のメーカーのもので引いてしまう。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

COMMANDS = """set system host-name 'rt-vyos-01'
set interfaces ethernet eth0 address '10.0.0.1/24'
set interfaces ethernet eth0 description 'to core'
set interfaces ethernet eth1 address 'dhcp'
set interfaces ethernet eth2 disable
set interfaces ethernet eth0 vif 10 address '10.0.10.1/24'
set protocols static route 0.0.0.0/0 next-hop '10.0.0.254'
"""

BRACES = """interfaces {
    ethernet eth0 {
        address 10.0.0.1/24
        description "to core"
    }
    ethernet eth1 {
        disable
    }
}
system {
    host-name rt-edge-01
}
"""


def _port(parsed, name: str):
    return next(port for port in parsed.interfaces if port.name == name)


def test_it_is_told_apart_from_junos() -> None:
    """取り違えると、保守期限も脆弱性も別のメーカーのもので引いてしまう。"""
    assert configparse.parse(COMMANDS).vendor is Vendor.VYOS


def test_junos_is_still_junos() -> None:
    junos = (
        "set system host-name mx-01\n"
        "set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24\n"
    )
    assert configparse.parse(junos).vendor is Vendor.JUNIPER


def test_the_port_keeps_the_name_the_machine_calls_it() -> None:
    """eth0 が本名。開いて Ethernet0 にすると、機器の画面と食い違う。"""
    parsed = configparse.parse(COMMANDS)
    assert {port.name for port in parsed.interfaces} == {"eth0", "eth1", "eth2", "eth0.10"}


def test_the_address_and_the_description_are_read() -> None:
    port = _port(configparse.parse(COMMANDS), "eth0")
    assert port.ip_address == "10.0.0.1/24"
    assert port.description == "to core"


def test_getting_the_address_from_dhcp_is_not_the_same_as_having_none() -> None:
    port = _port(configparse.parse(COMMANDS), "eth1")
    assert port.ip_address is None
    assert port.address_source == "dhcp"


def test_a_vlan_written_as_a_sub_port_becomes_a_vlan() -> None:
    """VLAN の定義を別に書かない書き方なので、ここからしか分からない。"""
    parsed = configparse.parse(COMMANDS)
    assert 10 in parsed.vlans
    assert _port(parsed, "eth0.10").vlan == 10


def test_a_disabled_port_is_not_counted_as_live() -> None:
    assert _port(configparse.parse(COMMANDS), "eth2").enabled is False


def test_the_route_is_read() -> None:
    routes = {route.destination: route.next_hop for route in configparse.parse(COMMANDS).routes}
    assert routes["0.0.0.0/0"] == "10.0.0.254"


def test_the_saved_shape_with_braces_reads_the_same() -> None:
    """2つの書き方で読み手を分けると、片方だけ直したときに食い違う。"""
    parsed = configparse.parse(BRACES)
    assert parsed.vendor is Vendor.VYOS
    assert parsed.hostname == "rt-edge-01"
    assert _port(parsed, "eth0").ip_address == "10.0.0.1/24"
    assert _port(parsed, "eth0").description == "to core"
    assert _port(parsed, "eth1").enabled is False
