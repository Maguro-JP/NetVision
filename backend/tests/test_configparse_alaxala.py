"""アラクサラ AX シリーズを読めること。

国内の企業ネットワークに残っている機種。書き方が IOS に近いぶん、目印の
無い config を横取りしないことのほうが大事になる。取り違えると、読めた値が
全部そのまま別物として出てしまい、しかも黙って通る。
"""

from __future__ import annotations

import pytest

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


OFFICIAL_RANGE = """\
! AX3650S
hostname "ax-core-01"
S/W: OS-L3A Ver. 12.1.A
vlan 100,200
!
interface range gigabitethernet 0/1-2
  switchport mode trunk
  switchport trunk allowed vlan 100,200
!
interface range gigabitethernet 0/11-13, gigabitethernet 0/21-22
  switchport mode access
  switchport access vlan 100
!
interface gigabitethernet 0/24
  switchport mode trunk
  switchport trunk allowed vlan add 100,200-210
"""


def test_a_range_written_once_gives_every_port_the_same_allowed_vlans():
    """出典: アラクサラ「Simple Configuration Examples for the AX Series」の設定例
    （`interface range gigabitethernet 0/1-2` に `switchport trunk allowed vlan 100,200`）と、
    Configuration Command Reference Vol.1 の「add/remove specification」
    （`interface range gigabitethernet 0/1-3, gigabitethernet 0/11-13`）。

    まとめて書いた2つ目以降を「許可 VLAN の指定なし」と読むと、
    絞ってあるトランクを「全部通す」と言ってしまう。
    """
    ports = {p.name: p for p in configparse.parse(OFFICIAL_RANGE).interfaces}
    for name in ("GigabitEthernet0/1", "GigabitEthernet0/2"):
        assert ports[name].trunk_vlans == [100, 200], name
        assert ports[name].trunk_all is False, name
    # 種別を要素ごとに書く並びも、そのまま全部のポートに行き渡る
    for name in ("GigabitEthernet0/11", "GigabitEthernet0/13", "GigabitEthernet0/22"):
        assert (ports[name].mode, ports[name].vlan) == ("access", 100), name


def test_the_range_does_not_raise_a_finding_about_unrestricted_trunks():
    """読み違いのままだと、直す必要の無いものを直させてしまう。"""
    from app.services import policyreview

    parsed = configparse.parse(OFFICIAL_RANGE)
    titles = [f.title for f in policyreview.review(parsed, OFFICIAL_RANGE)]
    assert not any("許可 VLAN を絞っていない" in title for title in titles), titles


def test_the_detailed_lldp_display_gives_the_neighbours_names():
    """`show lldp detail` から隣を読む。簡易表は相手が MAC でしか出ない。

    出典: アラクサラ AX8600R 運用コマンドレファレンス Vol.2「show lldp」
    図18-2（LLDP 設定および隣接装置情報の詳細表示）。
    """
    text = "\n".join(
        [
            "#show version",
            "Model AX3650S-24T4XW",
            "S/W: OS-L3A Ver. 12.1.A",
            "H/W: AX3650S-24T4XW",
            'hostname "ax01"',
            "!",
            "> show lldp detail",
            "Date 20XX/04/01 12:00:00 UTC",
            "Status: Enabled",
            "Chassis ID: Type=MAC Info=0012.e2c8.3c31",
            "System Name: LLDP1",
            "Port Counts=3",
            "Port 1/1 (CH: 10) Link: Up PortEnabled: TRUE",
            "  Port ID: Type=MAC Info=0012.e238.4cc0",
            "  Port Description: GigabitEther 1/1",
            "  Neighbor 1 TTL: 100",
            "    Chassis ID: Type=MAC Info=0012.e2c8.3c85",
            "    System Name: LLDP2",
            "    Port ID: Type=MAC Info=0012.e238.4cd1",
            "    Port Description: GigabitEther 1/12",
            "Port 1/2 Link: Down PortEnabled: FALSE",
            "  Neighbor Counts: 0",
            "Port 1/3 Link: Up PortEnabled: TRUE",
            "  Port Description: GigabitEther 1/3",
            "  Draft Neighbor 1 TTL: 100",
            "    Chassis ID: Type=MAC Info=0012.e268.2c21",
            "    System Name: LLDP3",
            "    Port Description: GigabitEther 1/5",
        ]
    )
    parsed = configparse.parse(text)
    found = {(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors}
    assert ("GigabitEthernet1/1", "LLDP2", "GigabitEther 1/12") in found
    assert ("GigabitEthernet1/3", "LLDP3", "GigabitEther 1/5") in found
    # 自分の名前（System Name: LLDP1）と、隣の居ない口は相手にしない
    assert not any(n.remote_hostname == "LLDP1" for n in parsed.neighbors)
    assert not any(n.local_interface == "GigabitEthernet1/2" for n in parsed.neighbors)


@pytest.mark.parametrize(
    ("rest", "interface", "distance"),
    [
        ("10.0.0.1 poll", None, None),
        ("10.0.0.1 noresolve", None, None),
        ("10.0.0.1 vlan 10", "Vlan10", None),
        ("10.0.0.1 vlan 10 5 tag 3 noresolve", "Vlan10", 5),
        ("10.0.0.1 vrf 20 3", None, 3),
    ],
)
def test_ax_static_route_words_are_not_ports_or_distances(rest, interface, distance):
    """経路の後ろの語を、口の名前やディスタンスと取り違えない。

    出典: アラクサラ「AX3660S コンフィグレーションコマンドレファレンス Vol.2」Ver.12.2 の ip route
    （`[<distance>] [weight <weight>] [tag <tag>] [{noinstall | reject}] [poll] [noresolve]`、
    ネクストホップの VRF は `{vrf <nexthop vrf id> | global}`）。
    """
    parsed = configparse.parse(
        f'hostname "ax01"\n! AX3660S\nip route 10.9.0.0 255.255.0.0 {rest}\n'
    )
    assert parsed.vendor is configparse.Vendor.ALAXALA
    [route] = parsed.routes
    assert (route.next_hop, route.interface, route.distance) == ("10.0.0.1", interface, distance)
