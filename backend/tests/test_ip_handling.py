"""IP アドレスの読み方を、間違えやすいところだけ集めて固定する。

同じ「10.0.0.1 0.0.0.0」でも、書いてある場所で意味が正反対になる。
ここを取り違えると、指摘も生成も静かに間違う。目で見て気づけないので、
形の違いを1つずつ試験で押さえる。
"""

import ipaddress

from app.services import calc, configparse
from app.services.aclcheck import network_of
from app.services.addition import netmask_of, wildcard_of
from app.services.configparse.routing import to_prefix

# ---------------------------------------------------------------------------
# ネットマスクとワイルドカードは、書いてある場所で決まる
# ---------------------------------------------------------------------------


def test_a_netmask_is_read_as_a_netmask():
    assert to_prefix("10.0.0.0", "255.255.255.0", wildcard=False) == "10.0.0.0/24"
    assert to_prefix("172.16.0.0", "255.240.0.0", wildcard=False) == "172.16.0.0/12"
    assert to_prefix("192.168.1.0", "255.255.255.128", wildcard=False) == "192.168.1.0/25"


def test_a_wildcard_is_read_as_a_wildcard():
    assert to_prefix("10.0.0.0", "0.0.0.255", wildcard=True) == "10.0.0.0/24"
    assert to_prefix("10.0.0.0", "0.255.255.255", wildcard=True) == "10.0.0.0/8"


def test_all_zeroes_means_the_opposite_in_the_two_places():
    """経路の `0.0.0.0 0.0.0.0` は「全部」、OSPF の `10.1.1.1 0.0.0.0` は「1台」。"""
    assert to_prefix("0.0.0.0", "0.0.0.0", wildcard=False) == "0.0.0.0/0"
    assert to_prefix("10.1.1.1", "0.0.0.0", wildcard=True) == "10.1.1.1/32"


def test_all_ones_means_the_opposite_in_the_two_places():
    assert to_prefix("10.0.0.5", "255.255.255.255", wildcard=False) == "10.0.0.5/32"
    assert to_prefix("0.0.0.0", "255.255.255.255", wildcard=True) == "0.0.0.0/0"


def test_a_mask_that_is_not_a_run_of_bits_is_refused():
    """255.0.255.0 のような飛び飛びの指定は、1つの帯にならない。"""
    assert to_prefix("10.0.0.0", "255.0.255.0", wildcard=False) is None


def test_the_address_and_mask_can_come_as_one_word():
    assert to_prefix("10.0.0.1 255.255.255.0") == "10.0.0.0/24"


def test_a_length_that_is_written_is_used_as_is():
    assert to_prefix("10.0.0.0/24") == "10.0.0.0/24"
    assert to_prefix("10.0.0.9", "/30") == "10.0.0.8/30"


def test_an_address_without_a_length_is_one_address():
    assert to_prefix("10.0.0.1") == "10.0.0.1/32"


def test_an_ipv6_address_without_a_length_is_one_address_too():
    """IPv6 の1つぶんは /128。ここを 32 と決め打つと、巨大な帯になる。"""
    assert to_prefix("2001:db8::1") == "2001:db8::1/128"
    assert to_prefix("2001:db8::/64") == "2001:db8::/64"
    assert to_prefix("fe80::1") == "fe80::1/128"


def test_something_that_is_not_an_address_is_not_guessed():
    assert to_prefix("any") is None
    assert to_prefix("host 10.0.0.5") is None
    assert to_prefix("") is None


# ---------------------------------------------------------------------------
# Cisco OSPF の network 行。現場でいちばん多い2つの書き方
# ---------------------------------------------------------------------------


def _ospf(*rows: str):
    text = "hostname r1\nrouter ospf 1\n" + "".join(f" {row}\n" for row in rows)
    return configparse.parse(text).ospf


def test_ospf_network_covering_a_single_interface():
    areas = _ospf("network 10.1.1.1 0.0.0.0 area 0")
    assert areas.areas[0].networks == ["10.1.1.1/32"]


def test_ospf_network_covering_everything():
    areas = _ospf("network 0.0.0.0 255.255.255.255 area 0")
    assert areas.areas[0].networks == ["0.0.0.0/0"]


def test_ospf_network_with_an_ordinary_wildcard():
    areas = _ospf("network 192.168.0.0 0.0.255.255 area 1")
    assert areas.areas[0].networks == ["192.168.0.0/16"]


def test_bgp_network_uses_a_netmask_not_a_wildcard():
    """BGP の network は mask で書く。ここをワイルドカードとして読むと逆になる。"""
    text = """hostname r1
router bgp 65001
 network 10.0.0.0 mask 255.255.255.0
 aggregate-address 10.0.0.0 255.0.0.0 summary-only
"""
    bgp = configparse.parse(text).bgp
    assert bgp.networks == ["10.0.0.0/24"]
    assert bgp.aggregates == ["10.0.0.0/8"]


def test_a_default_route_is_read_as_everything():
    text = "hostname r1\nip route 0.0.0.0 0.0.0.0 10.0.0.254\n"
    route = configparse.parse(text).routes[0]
    assert route.destination == "0.0.0.0/0"
    assert route.next_hop == "10.0.0.254"


def test_a_host_route_is_read_as_one_address():
    text = "hostname r1\nip route 10.9.9.9 255.255.255.255 10.0.0.254\n"
    assert configparse.parse(text).routes[0].destination == "10.9.9.9/32"


# ---------------------------------------------------------------------------
# ACL の書き方
# ---------------------------------------------------------------------------


def test_acl_wildcards_and_keywords():
    assert network_of("any") == ipaddress.ip_network("0.0.0.0/0")
    assert network_of("host 10.0.0.5") == ipaddress.ip_network("10.0.0.5/32")
    assert network_of("10.0.0.0 0.0.0.255") == ipaddress.ip_network("10.0.0.0/24")
    assert network_of("10.0.0.0/24") == ipaddress.ip_network("10.0.0.0/24")


def test_an_acl_wildcard_that_skips_bits_is_not_turned_into_a_range():
    """0.0.0.254 は飛び飛びを指せる。1つの帯として扱ってはいけない。"""
    assert network_of("10.0.0.0 0.0.0.254") is None


# ---------------------------------------------------------------------------
# アドレスとセグメントの取り出し
# ---------------------------------------------------------------------------


def test_the_address_and_its_segment_are_kept_apart():
    assert calc.address_and_network("10.0.0.1/24") == ("10.0.0.1", "10.0.0.0/24")
    assert calc.address_and_network("10.0.0.1 255.255.255.0") == ("10.0.0.1", "10.0.0.0/24")
    assert calc.address_and_network("2001:db8::1/64") == ("2001:db8::1", "2001:db8::/64")


def test_a_point_to_point_pair_keeps_both_addresses():
    """/31 は2台で使い切る。ネットワークに寄せると片方が消える。"""
    assert calc.address_and_network("10.0.0.0/31") == ("10.0.0.0", "10.0.0.0/31")
    assert calc.address_and_network("10.0.0.1/31") == ("10.0.0.1", "10.0.0.0/31")


def test_the_netmask_and_wildcard_of_a_segment():
    assert netmask_of("10.0.0.0/24") == "255.255.255.0"
    assert wildcard_of("10.0.0.0/24") == "0.0.0.255"
    assert netmask_of("10.0.0.0/30") == "255.255.255.252"
    assert wildcard_of("10.0.0.0/30") == "0.0.0.3"
