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
