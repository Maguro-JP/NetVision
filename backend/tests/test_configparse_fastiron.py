"""Ruckus / Brocade FastIron（ICX）を読めること。

見た目が IOS に近いので、これまでは Cisco として読んでいた。メーカーを
取り違えると保守期限も脆弱性も別のもので判定してしまう。加えて VLAN の
持ち方が IOS と逆なので、そのままでは VLAN も口の所属も空になる。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor

_ICX = "\n".join(
    [
        "SSH@icx1#show version",
        "  HW: Stackable ICX7150-48P",
        "  SW: Version 08.0.95cT213",
        "icx1#show running-config",
        "hostname icx1",
        "!",
        "vlan 10 name USERS by port",
        " tagged ethe 1/1/1",
        " untagged ethe 1/1/3 to 1/1/6",
        "!",
        "vlan 20 name VOICE by port",
        " tagged ethe 1/1/1",
        "!",
        "interface ethernet 1/1/1",
        " port-name to-core",
        "!",
        "interface ve 10",
        " ip address 10.0.10.1 255.255.255.0",
        "!",
        "ip route 0.0.0.0/0 10.0.10.254",
    ]
)


def test_model_version_and_vlans_are_read() -> None:
    parsed = configparse.parse(_ICX)
    assert parsed.vendor is Vendor.FASTIRON
    assert parsed.hostname == "icx1"
    assert parsed.model == "ICX7150-48P"
    assert parsed.os_version == "08.0.95cT213"
    assert parsed.vlans == {10: "USERS", 20: "VOICE"}


def test_membership_written_under_the_vlan_reaches_the_ports() -> None:
    ports = {item.name: item for item in configparse.parse(_ICX).interfaces}
    assert ports["Ethernet1/1/1"].description == "to-core"
    assert ports["Ethernet1/1/1"].mode == "trunk"
    assert ports["Ethernet1/1/1"].trunk_vlans == [10, 20]
    # `to` の範囲は1つずつにほどく。まとめたままだと空きポートが数えられない
    assert ports["Ethernet1/1/5"].vlan == 10
    assert ports["Ethernet1/1/5"].mode == "access"
    assert "Ethernet1/1/7" not in ports
    assert ports["Ve10"].ip_address == "10.0.10.1/24"


def test_a_cisco_config_is_not_taken_for_fastiron() -> None:
    text = "\n".join(
        [
            "version 17.9",
            "hostname c9300-01",
            "vlan 10",
            " name USERS",
            "interface GigabitEthernet1/0/1",
            " switchport access vlan 10",
        ]
    )
    assert configparse.detect_vendor(text) is Vendor.CISCO_IOS


def test_vrrp_and_the_stp_priority_are_read() -> None:
    """二重にしてある入口が「1台だけ」に見えないこと。

    FastIron は VRRP の中身を口の下に一段下げて書く。行だけを見ていると
    拾えず、落ちる範囲を数えるときにそのまま効く。
    """
    text = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "vlan 10 name USERS by port",
            " spanning-tree priority 4096",
            "interface ve 10",
            " ip address 10.0.10.2 255.255.255.0",
            " ip vrrp-extended vrid 10",
            "  backup priority 120",
            "  ip-address 10.0.10.1",
        ]
    )
    parsed = configparse.parse(text)
    assert len(parsed.redundancy) == 1
    entry = parsed.redundancy[0]
    assert entry.interface == "Ve10"
    assert entry.group == "10"
    assert entry.virtual_ip == "10.0.10.1"
    assert entry.priority == 120
    assert ("STP の優先度", "優先度: 4096") in {
        (item.name, item.detail) for item in parsed.features
    }


def test_the_bundle_is_read_from_the_lag_block() -> None:
    """FastIron は束ねの側に口を並べる。口だけ見ても所属が分からない。

    読めないと、2本で束ねてあるところが1本ずつに見え、落ちる範囲を数えた
    ときに実際より軽く出る。
    """
    text = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            'lag "core" dynamic id 1',
            " ports ethernet 1/1/1 to 1/1/2",
            " primary-port 1/1/1",
            " deploy",
        ]
    )
    ports = {item.name: item for item in configparse.parse(text).interfaces}
    assert ports["Ethernet1/1/1"].lag_group == 1
    assert ports["Ethernet1/1/2"].lag_group == 1
    # dynamic は LACP でやりとりする形
    assert ports["Ethernet1/1/1"].lag_mode == "active"


def test_the_routing_blocks_are_read_without_a_number_on_the_heading() -> None:
    """FastIron は見出しに番号を書かず、自分の AS は中の `local-as` に書く。

    見出しだけを見ていると、経路がまるごと読めていないことになる。
    """
    text = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "router ospf",
            " area 0",
            "interface ve 10",
            " ip address 10.0.10.1 255.255.255.0",
            " ip ospf area 0",
            "router bgp",
            " local-as 65001",
            " neighbor 10.0.0.2 remote-as 65002",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.ospf is not None
    assert parsed.bgp is not None
    assert parsed.bgp.local_as == "65001"
    assert [(item.address, item.remote_as) for item in parsed.bgp.neighbors] == [
        ("10.0.0.2", "65002")
    ]
    ports = {item.name: item for item in parsed.interfaces}
    assert ports["Ve10"].ospf_area == "0"


def test_a_port_named_inside_a_command_is_read_whole() -> None:
    """種別と番号が離れた口の名前を、行の途中でも1つの名前として読む。

    種別だけを拾うと、実在しない `ethernet` という口に変換を結び付けたまま
    黙って通る。
    """
    text = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "ip nat inside source list 1 interface ethernet 1/1/1 overload",
            "interface ethernet 1/1/1",
            " ip nat outside",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.nat is not None
    assert parsed.nat.rules[0].translated == "Ethernet1/1/1"
    assert parsed.nat.outside_interfaces == ["Ethernet1/1/1"]


def test_the_time_source_is_read_but_a_radius_host_is_not_mistaken_for_one() -> None:
    """FastIron は参照先を `ntp` の下に一段下げて書く。

    行だけを見て `server 10.0.0.5` を拾うと、RADIUS の並びまで時刻の参照先
    として数える。1台だけ参照先が違う、を見つける材料が狂う。
    """
    ntp = "  HW: Stackable ICX7150-48P\nhostname icx1\nntp\n server 10.0.99.10\n"
    radius = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "aaa group server radius RAD",
            " server 10.0.99.20",
        ]
    )

    found = {(item.name, item.detail) for item in configparse.parse(ntp).features}
    assert ("NTP", "参照先: 10.0.99.10") in found

    assert not [item for item in configparse.parse(radius).features if item.name == "NTP"]


def test_the_untagged_vlan_on_a_trunk_is_the_native_vlan() -> None:
    """ICX は VLAN の下に口を並べる。タグ付きと一緒に載ったタグ無しは native。

    アクセス VLAN として持つと、両端で食い違っていても比べられない。
    タグ無しの食い違いは、分けたはずのものが混ざる、いちばん気づきにくい形。
    """
    text = (
        "hostname icx-01\n!\n"
        "vlan 10 name USERS by port\n tagged ethe 1/1/1\n!\n"
        "vlan 20 name SRV by port\n tagged ethe 1/1/1\n!\n"
        "vlan 999 name NATIVE by port\n untagged ethe 1/1/1\n!\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10, 20]
    assert port.native_vlan == 999
    assert port.vlan is None


def test_the_order_the_vlans_are_written_in_does_not_matter() -> None:
    """タグ無しの行が先に来ることもある。読み終えてから決める。"""
    text = (
        "hostname icx-01\n!\n"
        "vlan 999 name NATIVE by port\n untagged ethe 1/1/1\n!\n"
        "vlan 10 name USERS by port\n tagged ethe 1/1/1\n!\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.mode == "trunk"
    assert port.native_vlan == 999


def test_a_port_with_only_an_untagged_vlan_is_a_port_for_a_device() -> None:
    """タグ無しだけなら、それは端末を挿す口。トランクにしてしまわない。"""
    text = "hostname icx-01\n!\nvlan 10 name USERS by port\n untagged ethe 1/1/5\n!\n"
    port = configparse.parse(text).interfaces[0]
    assert port.mode == "access"
    assert port.vlan == 10
    assert port.native_vlan is None


def test_ports_written_as_a_range_are_opened_one_by_one():
    """`interface ethernet 1/1/1 to 1/1/4` は4つの口。開かないと全部空になる。

    出典: RUCKUS FastIron Management Guide「Changing the Gbps fiber negotiation
    mode」（`device(config)# interface ethernet 1/1/1 to 1/1/4`、プロンプトが
    `config-mif-1/1/1-1/1/4` になる）。
    """
    text = "\n".join(
        [
            "ver 08.0.95",
            "hostname icx-01",
            "vlan 10 name USERS",
            "interface ethernet 1/1/1 to 1/1/4",
            " port-name office",
            " enable",
            "!",
            "interface ethernet 1/1/24",
            " enable",
            "!",
        ]
    )
    parsed = configparse.parse(text)
    ports = {i.name: i for i in parsed.interfaces}
    assert "Ethernet1/1/1" in ports
    assert "Ethernet1/1/4" in ports
    assert all(ports[f"Ethernet1/1/{n}"].description == "office" for n in (1, 2, 3, 4))
    assert ports["Ethernet1/1/24"].description is None
