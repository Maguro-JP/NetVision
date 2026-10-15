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


def test_the_saved_form_keeps_static_routes():
    """保存形式（config.boot）の静的経路を読み落とさない。

    VyOS は `route 0.0.0.0/0 { next-hop 192.0.2.254 { } }` のように、
    値そのものを囲みの見出しとして書く。中身が空の囲みは、それ自体が
    1つの設定（`set protocols static route 0.0.0.0/0 next-hop 192.0.2.254`）。

    出典: VyOS ドキュメント Static（set protocols static route <subnet>
    next-hop <address> / … next-hop <address> distance <distance>）
    """
    parsed = configparse.parse(
        "interfaces {\n"
        "    ethernet eth0 {\n"
        "        address 192.0.2.1/24\n"
        "    }\n"
        "}\n"
        "protocols {\n"
        "    static {\n"
        "        route 0.0.0.0/0 {\n"
        "            next-hop 192.0.2.254 {\n"
        "            }\n"
        "        }\n"
        "        route 10.9.0.0/16 {\n"
        "            next-hop 10.0.0.9 {\n"
        "                distance 5\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
        "system {\n"
        "    host-name vyos-01\n"
        "}\n"
    )
    assert parsed.hostname == "vyos-01"
    assert [(route.destination, route.next_hop) for route in parsed.routes] == [
        ("0.0.0.0/0", "192.0.2.254"),
        ("10.9.0.0/16", "10.0.0.9"),
    ]


def test_bonding_members_written_the_vyos_1_3_way_are_read():
    """VyOS は LAG の子を束ねる口の側に `member interface` で書く。子の口に束ねる番号を付ける。

    出典: VyOS 1.5.x (circinus) LTS Documentation「Bond / link aggregation」
    （`set interfaces bonding bond0 member interface eth0`）。
    旧来の `set interfaces ethernet eth0 bond-group bond0` も引き続き読む。
    """
    text = (
        "set system host-name 'vyos-01'\n"
        "set interfaces ethernet eth0 hw-id '00:00:5e:00:53:00'\n"
        "set interfaces bonding bond0 mode '802.3ad'\n"
        "set interfaces bonding bond0 member interface 'eth1'\n"
        "set interfaces bonding bond0 member interface eth2\n"
        "set interfaces bonding bond0 vif 10 address 192.168.0.1/24\n"
    )
    parsed = configparse.parse(text)
    lag = {i.name: i.lag_group for i in parsed.interfaces if i.lag_group is not None}
    assert lag == {"eth1": 0, "eth2": 0}


def test_a_route_out_of_an_interface_is_read() -> None:
    """次の宛先を口で書いた経路（`interface eth0`・`dhcp-interface eth0`）も取りこぼさない。

    出典: VyOS Documentation「Static」
    （`set protocols static route <subnet> interface <interface>`、
    `set protocols static route <subnet> dhcp-interface <interface>`、
    `set protocols static route6 <subnet> interface <interface>`）。
    `next-hop` の形は今までどおり読む（見張り）。blackhole・reject は読まない（未対応）。
    """
    text = "\n".join(
        [
            "set system host-name vy-rt",
            "set interfaces ethernet eth0 address 10.0.0.1/24",
            "set protocols static route 192.0.2.0/24 next-hop 10.0.0.254",
            "set protocols static route 198.51.100.0/24 interface eth0",
            "set protocols static route 203.0.113.0/24 dhcp-interface eth1",
            "set protocols static route6 2001:db8:1::/64 interface eth0",
        ]
    )
    routes = {r.destination: r for r in configparse.parse(text).routes}
    assert routes["192.0.2.0/24"].next_hop == "10.0.0.254"
    assert routes["198.51.100.0/24"].interface == "eth0"
    assert routes["198.51.100.0/24"].next_hop is None
    assert routes["203.0.113.0/24"].interface == "eth1"
    assert routes["2001:db8:1::/64"].interface == "eth0"
