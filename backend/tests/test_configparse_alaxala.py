"""アラクサラ AX シリーズを読めること。

国内の企業ネットワークに残っている機種。書き方が IOS に近いぶん、目印の
無い config を横取りしないことのほうが大事になる。取り違えると、読めた値が
全部そのまま別物として出てしまい、しかも黙って通る。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor

_SHOW_VERSION = "\n".join(
    [
        "#show version",
        "Model AX3650S-24T4XW",
        "S/W: OS-L3A Ver. 12.1.A",
        "H/W: AX3650S-24T4XW",
    ]
)

_CONFIG = "\n".join(
    [
        'hostname "ax01"',
        "!",
        "vlan 10",
        '  name "USERS"',
        "vlan 20",
        '  name "VOICE"',
        "!",
        "interface gigabitethernet 0/1",
        '  description "to-core"',
        "  switchport mode trunk",
        "  switchport trunk allowed vlan 10,20",
        "!",
        "interface gigabitethernet 0/2",
        "  switchport mode access",
        "  switchport access vlan 10",
        "!",
        "interface vlan 10",
        "  ip address 10.0.10.1 255.255.255.0",
        "!",
        "ip route 0.0.0.0 0.0.0.0 10.0.10.254",
    ]
)


def test_model_version_and_ports_are_read() -> None:
    parsed = configparse.parse(f"{_SHOW_VERSION}\n{_CONFIG}")
    assert parsed.vendor is Vendor.ALAXALA
    assert parsed.hostname == "ax01"
    assert parsed.model == "AX3650S-24T4XW"
    assert parsed.os_version == "12.1.A"
    assert parsed.vlans == {10: "USERS", 20: "VOICE"}

    ports = {item.name: item for item in parsed.interfaces}
    assert ports["GigabitEthernet0/1"].description == "to-core"
    assert ports["GigabitEthernet0/1"].trunk_vlans == [10, 20]
    assert ports["GigabitEthernet0/2"].vlan == 10
    assert ports["Vlan10"].ip_address == "10.0.10.1/24"
    assert [route.destination for route in parsed.routes] == ["0.0.0.0/0"]


def test_a_cisco_config_is_not_taken_for_alaxala() -> None:
    # 目印が無ければ名乗らない。IOS 風の書き方だけでは足りない
    text = "\n".join(
        [
            "version 17.9",
            "hostname c9300-01",
            "interface GigabitEthernet1/0/1",
            " switchport mode access",
            " switchport access vlan 10",
        ]
    )
    assert configparse.detect_vendor(text) is Vendor.CISCO_IOS


def test_the_config_alone_without_a_marker_is_not_claimed() -> None:
    # 型番も名乗りも無い抜粋。読めない、と言うほうが正しい
    assert configparse.detect_vendor(_CONFIG) is not Vendor.ALAXALA


def test_a_port_named_inside_a_command_is_read_whole() -> None:
    text = "\n".join(
        [
            "Model AX3650S-24T4XW",
            'hostname "ax01"',
            "ip nat inside source list 1 interface gigabitethernet 0/1 overload",
            "interface gigabitethernet 0/1",
            " ip nat outside",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.nat is not None
    assert parsed.nat.rules[0].translated == "GigabitEthernet0/1"


def test_the_official_show_version_sample_is_read():
    """公式の出力例をそのまま読む。

    出典: AX3800S/AX3650S ソフトウェアアップグレードガイド（alaxala.com）の
    `show version` 例。Model / S/W / H/W の3行。
    """
    text = (
        "> show version\n"
        "Date 20XX/12/19 11:21:01 UTC\n"
        "Model: AX3630S-24T\n"
        "S/W: OS-L3L-A Ver. 10.0\n"
        "H/W: AX-3630S-24T-ZX [TA036L24P000S03015AD004:80320101:1183:2B504]\n"
        ">\n"
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.ALAXALA
    assert (parsed.model, parsed.os_name, parsed.os_version) == ("AX3630S-24T", "OS-L3L-A", "10.0")
