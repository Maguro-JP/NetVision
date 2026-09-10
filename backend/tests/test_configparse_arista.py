"""Arista EOS を読めること。

見た目は Cisco IOS に近い。そのまま同じ扱いにすると、アドレスと経路の
書き方（長さ付き）と、BGP の上限の既定値のところで落ちる。そこを見る。
"""

from pathlib import Path

from app.services import configparse, policyreview
from app.services.configparse import arista
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load() -> str:
    return (FIXTURES / "arista-dc-01.txt").read_text()


def parsed():
    return configparse.parse(load())


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text), text)]


def test_it_is_detected_and_the_model_comes_from_the_header():
    """機種と版は、見出しの1行にしか出てこないことがある。"""
    p = parsed()
    assert p.vendor is Vendor.ARISTA
    assert (p.hostname, p.model, p.os_version, p.os_name) == (
        "sw-arista-01",
        "DCS-7050SX3-48YC8",
        "4.29.2F",
        "EOS",
    )


def test_addresses_written_with_a_length_are_read():
    ports = {i.name: i for i in parsed().interfaces}
    assert ports["Vlan10"].ip_address == "10.0.10.1/24"
    assert ports["Ethernet1"].mode == "trunk"
    assert ports["Ethernet1"].trunk_vlans == [10, 20]


def test_routes_written_with_a_length_are_read():
    """IOS の読み手は `ip route 0.0.0.0/0 ...` を拾えない。"""
    routes = {r.destination: r for r in parsed().routes}
    assert routes["0.0.0.0/0"].next_hop == "10.0.10.254"
    assert routes["10.9.0.0/16"].distance == 200


def test_the_written_maximum_routes_is_used():
    neighbor = parsed().bgp.neighbors[0]
    assert (neighbor.address, neighbor.remote_as) == ("10.0.10.2", "65002")
    assert neighbor.maximum_prefix == 30000


def test_a_neighbor_without_the_line_still_has_the_default_limit():
    """EOS は書かなくても既定で上限が掛かる。IOS の目で見ると誤って言う。"""
    text = load().replace("   neighbor 10.0.10.2 maximum-routes 30000\n", "")
    neighbor = configparse.parse(text).bgp.neighbors[0]
    assert neighbor.maximum_prefix == arista.DEFAULT_MAXIMUM_ROUTES
    assert not [t for t in titles(text) if "上限がありません" in t]


def test_maximum_routes_zero_really_means_no_limit():
    text = load().replace("maximum-routes 30000", "maximum-routes 0")
    assert configparse.parse(text).bgp.neighbors[0].maximum_prefix is None
    assert [t for t in titles(text) if "上限がありません" in t]


def test_nothing_is_said_about_neighbor_logging():
    """EOS は書かなくても相手の状態変化を記録する。"""
    assert not [t for t in titles(load()) if "ログに残りません" in t]


def test_a_clean_config_gets_no_findings():
    assert titles(load()) == []


def test_an_ios_config_is_not_mistaken_for_eos():
    ios = "hostname sw-01\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n"
    assert configparse.parse(ios).vendor is Vendor.CISCO_IOS


def test_the_lldp_table_gives_the_lines_for_the_diagram():
    """`show lldp neighbors` の表。図の線はここから引く。略された Et1 は正式名に伸ばす。"""
    pairs = [(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed().neighbors]
    assert ("Ethernet1", "core-sw-01", "Ethernet1/0/1") in pairs
    assert ("Ethernet2", "server-01", "Ethernet0") in pairs


def test_a_static_route_is_not_counted_twice() -> None:
    # IOS の読み手と EOS の読み手が同じ行を拾うため、重複しやすい
    text = "\n".join(
        [
            "! device: sw1 (DCS-7050SX3-48YC8, EOS-4.29.2F)",
            "hostname sw1",
            "ip route 0.0.0.0/0 10.0.10.254",
        ]
    )
    parsed = configparse.parse(text)
    assert len(parsed.routes) == 1


OFFICIAL_VARP = """\
! device: leaf-3a (DCS-7050SX3-48YC8, EOS-4.28.3M)
hostname leaf-3a
!
ip virtual-router mac-address 00:1c:73:00:00:99
!
interface Vlan310
    description Access_Pod3-Data
    no autostate
    ip address 10.3.10.2/24
    ip virtual-router address 10.3.10.1
    ip helper-address 10.100.0.0
!
interface Vlan320
    description Access_Pod3-Voice
    ip address 10.3.20.2/24
    ip virtual-router address 10.3.20.1
"""


def test_varp_is_read_as_the_gateway_redundancy_it_is():
    """出典: Arista の公式ラボガイド（Layer 3 Leaf-Spine）の設定例。

    グローバルに `ip virtual-router mac-address` を置き、SVI に
    `ip virtual-router address 10.3.10.1` を書く。2台とも同じ仮想アドレスで
    同時に応答するので、VRRP と違って主と待機もグループ番号も無い。
    読まないと、端末の出口が資料から丸ごと消える。
    """
    parsed = configparse.parse(OFFICIAL_VARP)
    entries = {entry.interface: entry for entry in parsed.redundancy}
    assert set(entries) == {"Vlan310", "Vlan320"}
    assert entries["Vlan310"].protocol == "varp"
    assert entries["Vlan310"].virtual_ip == "10.3.10.1"
    assert entries["Vlan310"].group is None


def test_a_varp_address_outside_the_segment_is_caught():
    from app.services import policyreview

    text = OFFICIAL_VARP.replace(
        "ip virtual-router address 10.3.10.1", "ip virtual-router address 10.9.9.1"
    )
    titles = [f.title for f in policyreview.review(configparse.parse(text), text)]
    assert any("VARP の仮想アドレス 10.9.9.1" in title for title in titles), titles
