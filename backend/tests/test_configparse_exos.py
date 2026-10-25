"""Extreme EXOS を読む。

`interface` の囲みが無く、`configure` で始まる1行ずつの命令が並ぶ。
IOS の目で読むと、口も VLAN も1つも取れない。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

EXOS = """configure snmp sysName "sw-exos-01"
create vlan "USERS" tag 10
create vlan "MGMT" tag 30
create vlan "NATIVE" tag 999
configure vlan USERS add ports 1-20 untagged
configure vlan USERS add ports 24 tagged
configure vlan MGMT add ports 24 tagged
configure vlan NATIVE add ports 24 untagged
configure vlan MGMT ipaddress 10.0.0.5 255.255.255.0
configure ports 1 display-string "shitsumu"
disable ports 21
enable sharing 23 grouping 23-24 algorithm address-based L3 lacp
configure iproute add default 10.0.0.1
configure iproute add 192.168.0.0 255.255.0.0 10.0.0.254
"""


def _parsed():
    return configparse.parse(EXOS)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_it_is_recognised() -> None:
    parsed = _parsed()
    assert parsed.vendor is Vendor.EXOS
    assert parsed.hostname == "sw-exos-01"


def test_the_vlans_and_their_numbers_are_read() -> None:
    """口は VLAN の名前で足される。番号と結び付かないと口が取れない。"""
    assert _parsed().vlans == {10: "USERS", 30: "MGMT", 999: "NATIVE"}


def test_a_port_added_to_a_vlan_becomes_a_port() -> None:
    port = _port("Port1")
    assert port.mode == "access"
    assert port.vlan == 10
    assert port.description == "shitsumu"


def test_the_untagged_vlan_on_a_tagged_port_is_the_native_vlan() -> None:
    port = _port("Port24")
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10, 30]
    assert port.native_vlan == 999


def test_when_tagged_is_not_written_it_means_untagged() -> None:
    """EXOS の既定はタグ無し。書いていないものをトランクにしてしまわない。"""
    assert _port("Port20").mode == "access"


def test_a_disabled_port_is_not_counted_as_live() -> None:
    assert _port("Port21").enabled is False


def test_bundled_ports_are_read() -> None:
    assert _port("Port23").lag_group == 23
    assert _port("Port24").lag_mode == "active"


def test_the_address_on_a_vlan_is_read() -> None:
    assert _port("Vlan30").ip_address == "10.0.0.5/24"


def test_the_routes_are_read() -> None:
    routes = {route.destination: route.next_hop for route in _parsed().routes}
    assert routes["0.0.0.0/0"] == "10.0.0.1"
    assert routes["192.168.0.0/16"] == "10.0.0.254"


def test_no_commands_are_offered_for_a_machine_we_cannot_write() -> None:
    """囲みが無く、消し方も語が変わる。IOS の規則で出すと入らない形になる。"""
    from app.services.configdiff import build

    diff = build(EXOS, EXOS + "configure vlan USERS add ports 22 untagged\n", vendor=Vendor.EXOS)
    assert not diff.script.strip()
    assert diff.warnings


def test_the_detailed_lldp_display_gives_the_neighbours_names():
    """`show lldp neighbors detailed` から隣を読む。簡易表は相手が MAC だけ。

    出典: ExtremeXOS User Guide「show lldp neighbors」の実行例
    （`LLDP Port 1:2 detected 1 neighbor` / `Port ID     : "1:1"` /
    `- System Name: "System name 1"`）。
    """
    text = "\n".join(
        [
            "create vlan USERS",
            "configure vlan USERS tag 10",
            "configure snmp sysName sw-exos-01",
            "",
            "# show lldp all neighbors detailed",
            "-----------------------------------------------------------------",
            "LLDP Port 1:2 detected 1 neighbor",
            "  Neighbor: 00:04:96:26:A4:70/1:1, age 12 seconds",
            "    - Chassis ID type: MAC address (4)",
            "      Chassis ID     : 00:04:96:26:A4:70",
            "    - Port ID type: ifName (5)",
            '      Port ID     : "1:1"',
            "    - Time To Live: 120 seconds",
            '    - System Name: "core-sw-01"',
            "-----------------------------------------------------------------",
            "LLDP Port 2:6 detected 1 neighbor",
            "  Neighbor: (5.1)10.201.41.146/00:04:0D:EC:EA:5C, age 8 seconds",
            "    - Time To Live: 120 seconds",
            "-----------------------------------------------------------------",
        ]
    )
    parsed = configparse.parse(text)
    found = {(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors}
    assert ("1:2", "core-sw-01", "1:1") in found
    # 名前が出ていない相手は作らない
    assert not any(n.local_interface == "2:6" for n in parsed.neighbors)


def test_a_mask_cut_off_mid_line_is_skipped_rather_than_failing() -> None:
    """貼り付けが途中で切れた行で、Config 全体を読めなくしない。

    切れたマスク（`255.255.255.`）から長さを作ると誤った住所になるので、その行は読まない。
    """
    parsed = configparse.parse(
        "# Module devmgr configuration.\n"
        'configure snmp sysName "exos-sw1"\n'
        'create vlan "MGMT"\n'
        "configure vlan MGMT tag 10\n"
        'create vlan "USERS"\n'
        "configure vlan USERS tag 20\n"
        "configure vlan MGMT ipaddress 10.0.0.5 255.255.255.\n"
        "configure vlan USERS ipaddress 10.0.20.1 255.255.255.0\n",
        Vendor.EXOS,
    )
    addresses = {i.name: i.ip_address for i in parsed.interfaces if i.ip_address}
    assert addresses == {"Vlan20": "10.0.20.1/24"}


def test_ports_added_to_a_vlan_by_its_number_are_read() -> None:
    """VLAN は名前のほか、番号の並びでも指定できる（`configure vlan 10,20 add ports 5 tagged`）。

    出典: ExtremeXOS Command Reference Guide 22.1 / 31.7、Switch Engine v33.2.1 Command References
    の configure vlan add ports（`configure [{vlan} vlan_name | vlan vlan_list] add ports ...`、
    「The vlan_list option was added in ExtremeXOS 16.1.」）。
    """
    text = (
        'configure snmp sysName "sw-exos"\n'
        "create vlan 10\n"
        "create vlan 20\n"
        "configure vlan 10,20 add ports 5 tagged\n"
        "configure vlan 30 add ports 6 untagged\n"
    )
    ports = {p.name: p for p in configparse.parse(text).interfaces}
    assert ports["Port5"].mode == "trunk"
    assert ports["Port5"].trunk_vlans == [10, 20]
    assert ports["Port6"].vlan == 30


def test_the_description_string_is_read() -> None:
    """口の説明は `display-string`（短い表示名）のほか `description-string` にも書ける。

    両方あれば長い方（description-string）を説明に持つ。

    出典: ExtremeXOS Command Reference Guide 30.1 の一覧（configure port description-string /
    configure ports display-string）。
    """
    text = (
        'configure snmp sysName "sw-exos"\n'
        "configure ports 1 display-string uplink\n"
        'configure ports 1 description-string "Uplink to core-sw-01 Te1/1/1"\n'
        'configure ports 2 description-string "Printer"\n'
    )
    ports = {p.name: p for p in configparse.parse(text).interfaces}
    assert ports["Port1"].description == "Uplink to core-sw-01 Te1/1/1"
    assert ports["Port2"].description == "Printer"


def test_sharing_follows_the_command_reference() -> None:
    """見張り: `enable sharing <port> grouping <port_list> ... {lacp | health-check}` を読む。

    出典: ExtremeXOS v33.1.1 Command References の enable sharing grouping
    （`enable sharing port grouping port_list {algorithm [...]} ... {lacp | health-check}`、
    port は「the master logical port」、port_list は「one or more ports or slots and ports」）。
    lacp は動的（active）、health-check と何も付けない形は静的（on）として持つ。
    """
    text = "\n".join(
        [
            "# ExtremeXOS version 33.1.1",
            'configure snmp sysName "x-stack"',
            "enable sharing 1:23 grouping 1:23-24 algorithm address-based L2 lacp",
            "enable sharing 2:1 grouping 2:1,2:3 algorithm address-based L3_L4 health-check",
            "enable sharing 9 grouping 9-10",
        ]
    )
    ports = {p.name: p for p in configparse.parse(text).interfaces}
    assert ports["Port1:23"].lag_group == ports["Port1:24"].lag_group == 23
    assert ports["Port1:24"].lag_mode == "active"
    assert ports["Port2:3"].lag_group == 1
    assert ports["Port2:3"].lag_mode == "on"
    assert ports["Port10"].lag_group == 9
    assert ports["Port10"].lag_mode == "on"


def test_ipv6_routes_are_read() -> None:
    """IPv6 の既定経路と静的経路を読む。IPv4 の形は今までどおり（見張り）。

    出典: ExtremeXOS 22.5 Command Reference の configure iproute add default
    （`[ gateway | ipv6Gateway | ipv6ScopedGateway ]`、
    例 `configure iproute add default 2001:db8::1234:5678`）、configure iproute add (IPV6)
    （`configure iproute add ipv6Netmask [ ipv6Gateway | ipv6ScopedGateway ]`、
    例 `configure iproute add 2001:db8:0:1111::/64 fe80::1111%default`）、
    configure iproute add (IPv4)（例 `configure iproute add 10.1.1.0/24 123.45.67.1`）。
    スコープ付きの次の宛先（`%default` は VLAN 名）は、アドレスの部分だけを持つ。
    """
    text = "\n".join(
        [
            "# ExtremeXOS version 22.5.1",
            'configure snmp sysName "x-v6"',
            "configure iproute add 10.1.1.0/24 123.45.67.1",
            "configure iproute add default 2001:db8::1234:5678",
            "configure iproute add 2001:db8:0:1111::/64 fe80::1111%default",
        ]
    )
    routes = {r.destination: r.next_hop for r in configparse.parse(text).routes}
    assert routes["10.1.1.0/24"] == "123.45.67.1"
    assert routes["::/0"] == "2001:db8::1234:5678"
    assert routes["2001:db8:0:1111::/64"] == "fe80::1111"


def test_vlan_addresses_follow_the_command_reference() -> None:
    """VLAN の住所は、`vlan` を省いた形と VLAN の番号で書いた形でも読む。

    出典: ExtremeXOS Command Reference Guide 32.4 の configure vlan ipaddress
    （`configure [{vlan} vlan_name | vlan vlan_id] ipaddress [ipaddress {netmask} |
    {ipNetmask} ...]`。
    作例は `configure vlan accounting ipaddress 10.12.123.1/24` と
    `configure vlan accounting ipaddress 10.12.123.1 255.255.255.0` が同じ意味）。
    """
    parsed = configparse.parse(
        'configure snmp sysName "exos-sw1"\n'
        'create vlan "accounting" tag 30\n'
        'create vlan "sales" tag 40\n'
        "create vlan 50\n"
        "configure vlan accounting ipaddress 10.12.123.1/24\n"
        "configure sales ipaddress 10.12.124.1 255.255.255.0\n"
        "configure vlan 50 ipaddress 10.12.125.1/31\n",
        Vendor.EXOS,
    )
    addresses = {i.name: i.ip_address for i in parsed.interfaces if i.ip_address}
    assert addresses == {
        "Vlan30": "10.12.123.1/24",
        "Vlan40": "10.12.124.1/24",
        "Vlan50": "10.12.125.1/31",
    }


def test_ports_are_added_to_a_vlan_written_without_the_vlan_keyword() -> None:
    """名前で書いた VLAN は `vlan` を省ける（`configure USERS add ports 1-2 untagged`）。

    出典: ExtremeXOS Command Reference Guide 22.1 / 31.7 の configure vlan add ports
    （`configure [{vlan} vlan_name | vlan vlan_list] add ports ...`。`{vlan}` は省ける語）。
    """
    parsed = configparse.parse(
        'configure snmp sysName "exos-sw1"\n'
        'create vlan "USERS" tag 20\n'
        'create vlan "VOICE" tag 30\n'
        "configure USERS add ports 1-2 untagged\n"
        "configure VOICE add ports 2 tagged\n",
        Vendor.EXOS,
    )
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["Port1"].vlan == 20
    assert ports["Port2"].trunk_vlans == [30]
    assert ports["Port2"].native_vlan == 20
