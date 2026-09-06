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
