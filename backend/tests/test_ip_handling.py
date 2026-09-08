"""IP アドレスの読み方を、間違えやすいところだけ集めて固定する。

同じ「10.0.0.1 0.0.0.0」でも、書いてある場所で意味が正反対になる。
ここを取り違えると、指摘も生成も静かに間違う。目で見て気づけないので、
形の違いを1つずつ試験で押さえる。
"""

import ipaddress

from app.services import calc, configparse, policyreview
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


# ---------------------------------------------------------------------------
# 1つのポートに複数のアドレスが付く形
# ---------------------------------------------------------------------------

_MULTI = """hostname r1
!
interface GigabitEthernet0/0
 ip address 10.0.0.1 255.255.255.0
 ip address 10.0.1.1 255.255.255.0 secondary
!
interface GigabitEthernet0/1
 ip address dhcp
!
interface GigabitEthernet0/2
 ipv6 address 2001:DB8::1/64
!
interface GigabitEthernet0/3
 ip address 10.0.9.1 255.255.255.0
 ipv6 address 2001:DB8:9::1/64
!
interface GigabitEthernet0/4
 ip unnumbered Loopback0
!
"""


def _ports(text: str):
    return {item.name: item for item in configparse.parse(text).interfaces}


def test_a_secondary_address_does_not_overwrite_the_first_one():
    """上書きすると、その機器が本来名乗っているアドレスが台帳から消える。"""
    port = _ports(_MULTI)["GigabitEthernet0/0"]
    assert port.ip_address == "10.0.0.1/24"
    assert port.extra_addresses == ["10.0.1.1/24"]


def test_dhcp_is_not_kept_as_an_address():
    """`dhcp` は住所ではない。住所として持つと、そのまま台帳に載ってしまう。"""
    assert _ports(_MULTI)["GigabitEthernet0/1"].ip_address is None


def test_an_ipv6_only_port_keeps_its_address():
    assert _ports(_MULTI)["GigabitEthernet0/2"].ip_address == "2001:DB8::1/64"


def test_a_port_with_both_families_keeps_the_v4_one_first():
    port = _ports(_MULTI)["GigabitEthernet0/3"]
    assert port.ip_address == "10.0.9.1/24"
    assert port.extra_addresses == ["2001:DB8:9::1/64"]


def test_a_port_borrowing_another_ports_address_has_none_of_its_own():
    assert _ports(_MULTI)["GigabitEthernet0/4"].ip_address is None


def test_the_length_written_form_is_read_too():
    """Arista や AOS-CX は長さで書く。"""
    text = "hostname sw1\ninterface Vlan10\n   ip address 10.0.10.1/24\n"
    assert _ports(text)["Vlan10"].ip_address == "10.0.10.1/24"


# ---------------------------------------------------------------------------
# 既定ゲートウェイの冗長。端末はこの仮想アドレスを出口として使う
# ---------------------------------------------------------------------------


def _virtual(cfg: str):
    from app.services import policyreview

    return [
        f for f in policyreview.review(configparse.parse(cfg), cfg) if "仮想アドレス" in f.title
    ]


def _hsrp(virtual: str, own: str = "10.0.10.2") -> str:
    return (
        "hostname r1\ninterface Vlan10\n"
        f" ip address {own} 255.255.255.0\n standby 10 ip {virtual}\n"
    )


def test_a_virtual_gateway_inside_the_segment_says_nothing():
    assert _virtual(_hsrp("10.0.10.1")) == []


def test_a_virtual_gateway_outside_the_segment_is_called_out():
    from app.services.reviewnote import HIGH

    found = _virtual(_hsrp("10.0.99.1"))
    assert len(found) == 1
    assert found[0].level == HIGH
    assert "帯の外" in found[0].title


def test_a_virtual_gateway_equal_to_the_device_address_is_called_out():
    assert "自身のアドレスと同じ" in _virtual(_hsrp("10.0.10.2"))[0].title


def test_a_virtual_gateway_on_the_edge_of_the_segment_is_called_out():
    assert "配れない" in _virtual(_hsrp("10.0.10.0"))[0].title
    assert "配れない" in _virtual(_hsrp("10.0.10.255"))[0].title


def test_vrrp_written_the_necqx_way_is_checked_too():
    cfg = (
        "sysname s1\ninterface Vlan-interface10\n"
        " ip address 10.0.10.2 255.255.255.0\n vrrp vrid 10 virtual-ip 10.0.77.1\n"
    )
    assert "帯の外" in _virtual(cfg)[0].title


# ---------------------------------------------------------------------------
# 静的経路の次の相手に、そもそも行けるか
# ---------------------------------------------------------------------------

_ROUTES = """hostname r1
interface GigabitEthernet0/0
 ip address 203.0.113.2 255.255.255.252
!
interface Vlan10
 ip address 10.0.10.1 255.255.255.0
!
"""


def _routes(*rows: str):
    from app.services import policyreview

    cfg = _ROUTES + "".join(f"{row}\n" for row in rows)
    return [f for f in policyreview.review(configparse.parse(cfg), cfg) if "次の相手" in f.title]


def test_a_next_hop_on_a_connected_segment_is_fine():
    assert _routes("ip route 0.0.0.0 0.0.0.0 203.0.113.1") == []
    assert _routes("ip route 10.20.0.0 255.255.0.0 10.0.10.9") == []


def test_a_next_hop_nobody_can_reach_is_called_out():
    from app.services.reviewnote import HIGH, MEDIUM

    # 逃げ道になる既定経路が無いので、この宛先はここで落ちる
    found = _routes("ip route 10.30.0.0 255.255.0.0 192.168.77.1")
    assert len(found) == 1
    assert found[0].level == HIGH
    assert "192.168.77.1" in found[0].title

    # 行ける既定経路があれば、落ちはせずそちらへ流れる
    with_default = _routes(
        "ip route 0.0.0.0 0.0.0.0 10.0.10.9",
        "ip route 10.30.0.0 255.255.0.0 192.168.77.1",
    )
    assert [f.level for f in with_default] == [MEDIUM]


def test_a_next_hop_reached_through_another_route_is_fine():
    """直結でなくても、他の経路で届くなら使われる。"""
    assert (
        _routes(
            "ip route 10.20.0.0 255.255.0.0 10.0.10.9",
            "ip route 172.16.0.0 255.255.0.0 10.20.0.1",
        )
        == []
    )


def test_a_default_route_is_not_used_as_the_reason_it_is_reachable():
    """既定経路は何でも覆う。それを根拠にすると、書き間違いを全部見逃す。"""
    assert (
        len(
            _routes(
                "ip route 0.0.0.0 0.0.0.0 203.0.113.1",
                "ip route 10.30.0.0 255.255.0.0 192.168.77.1",
            )
        )
        == 1
    )


def test_a_route_written_with_an_exit_port_needs_no_next_hop():
    assert _routes("ip route 10.40.0.0 255.255.0.0 GigabitEthernet0/0") == []


def test_nothing_is_said_when_the_ports_could_not_be_read():
    from app.services import policyreview

    cfg = "hostname r1\nip route 10.30.0.0 255.255.0.0 192.168.77.1\n"
    assert [
        f for f in policyreview.review(configparse.parse(cfg), cfg) if "次の相手" in f.title
    ] == []


# ---------------------------------------------------------------------------
# VLAN の並べ書き（Cisco）
# ---------------------------------------------------------------------------


def test_vlans_written_as_a_list_are_all_defined():
    """`vlan 10,20,30` を読み落とすと、その VLAN が「台帳にしかない」ことになる。"""
    assert sorted(configparse.parse("hostname x\nvlan 10,20,30\n").vlans) == [10, 20, 30]


def test_vlans_written_as_a_range_are_all_defined():
    assert sorted(configparse.parse("hostname x\nvlan 100-103\n").vlans) == [100, 101, 102, 103]


def test_a_single_vlan_still_takes_its_name():
    assert configparse.parse("hostname x\nvlan 10\n name USERS\n").vlans == {10: "USERS"}


def test_a_list_of_vlans_has_no_single_name_to_take():
    """並べ書きに name は書けない。あとの行の name を先頭に付けてはいけない。"""
    parsed = configparse.parse("hostname x\nvlan 10,20\n!\nvlan 30\n name SERVERS\n")
    assert parsed.vlans == {10: "", 20: "", 30: "SERVERS"}


def test_something_that_is_not_a_vlan_list_is_left_alone():
    assert configparse.parse("hostname x\nvlan internal allocation policy ascending\n").vlans == {}


def test_a_voice_vlan_counts_as_carried_by_the_port():
    """IP 電話の VLAN を数え落とすと、その VLAN がどこにも使われていないことになる。"""
    text = """hostname sw1
vlan 10
 name USERS
!
vlan 20
 name VOICE
!
interface GigabitEthernet0/1
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 20
!
"""
    port = configparse.parse(text).interfaces[0]
    assert port.vlan == 10
    assert port.voice_vlan == 20
    assert port.carried_vlans() == {10, 20}


def test_a_voice_vlan_without_a_definition_is_called_out():
    from app.services import configcheck

    text = """hostname sw1
vlan 10
 name USERS
!
interface GigabitEthernet0/1
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 20
!
"""
    parsed = configparse.parse(text)
    findings = configcheck._layer2(parsed)
    assert any("定義が無い VLAN" in f.title for f in findings), [f.title for f in findings]


def test_a_stack_tells_its_model_even_without_show_version():
    """型番が空だと、期限もリファレンスから引けない。"""
    text = "hostname sw1\nswitch 1 provision c9300-48p\nswitch 2 provision c9300-48p\n"
    assert configparse.parse(text).model == "C9300-48P"


def test_show_version_still_wins_over_the_stack_line():
    text = "hostname sw1\nswitch 1 provision c9300-48p\n!\nModel Number : C9300-24T\n"
    assert configparse.parse(text).model == "C9300-24T"


def test_a_router_subinterface_carries_the_vlan_it_tags():
    """ルーターの副ポートは、この行が VLAN の所在を示す唯一の手掛かりになる。"""
    text = """hostname rt1
interface GigabitEthernet0/0/0.10
 encapsulation dot1Q 10
 ip address 10.0.10.1 255.255.255.0
!
interface GigabitEthernet0/0/0.20
 encapsulation dot1q 20
 ip address 10.0.20.1 255.255.255.0
!
interface GigabitEthernet0/0/0.99
 encapsulation dot1Q 99 native
!
"""
    ports = {item.name: item for item in configparse.parse(text).interfaces}
    assert ports["GigabitEthernet0/0/0.10"].vlan == 10
    assert ports["GigabitEthernet0/0/0.20"].vlan == 20
    assert ports["GigabitEthernet0/0/0.99"].native_vlan == 99
    assert ports["GigabitEthernet0/0/0.10"].carried_vlans() == {10}


# ---------------------------------------------------------------------------
# Config と一緒に貼られる show の出力
# ---------------------------------------------------------------------------

_VLAN_BRIEF = """hostname sw1
interface GigabitEthernet0/1
 switchport mode access
!
show vlan brief

VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Gi0/24
10   USERS                            active    Gi0/1, Gi0/2, Gi0/3
20   SERVERS                          active    Gi0/10, Gi0/11, Gi0/12, Gi0/13,
                                                Gi0/14
999  UNUSED                           active

"""


def test_the_vlan_table_gives_the_definitions():
    """Config に vlan を書かない機種でも、この表があれば台帳を埋められる。"""
    assert configparse.parse(_VLAN_BRIEF).vlans == {
        1: "",
        10: "USERS",
        20: "SERVERS",
        999: "UNUSED",
    }


def test_the_vlan_table_says_which_ports_are_in_each_vlan():
    ports = {item.name: item for item in configparse.parse(_VLAN_BRIEF).interfaces}
    assert ports["GigabitEthernet0/2"].vlan == 10
    assert ports["GigabitEthernet0/10"].vlan == 20
    # 口が多くて次の行へ折り返した分も拾う
    assert ports["GigabitEthernet0/14"].vlan == 20


def test_a_trunk_port_is_not_turned_into_an_access_port_by_the_table():
    """表には主VLANしか出ない。Config で読んだトランクを上書きしない。"""
    text = _VLAN_BRIEF.replace(
        "interface GigabitEthernet0/1\n switchport mode access\n",
        "interface GigabitEthernet0/1\n switchport mode trunk\n"
        " switchport trunk allowed vlan 10,20\n",
    )
    port = {item.name: item for item in configparse.parse(text).interfaces}["GigabitEthernet0/1"]
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10, 20]


def test_a_config_without_the_table_is_unchanged():
    assert configparse.parse("hostname sw1\nvlan 10\n name USERS\n").vlans == {10: "USERS"}


def test_route_keywords_do_not_become_the_distance() -> None:
    """name / tag / track の後ろの値を、距離やインタフェースと取り違えない。"""
    text = "\n".join(
        [
            "hostname r1",
            "ip route 0.0.0.0 0.0.0.0 10.0.10.254 name DEFAULT 100",
            "ip route 10.20.0.0 255.255.0.0 GigabitEthernet0/1 10.0.10.253 track 10",
            "ip route 10.30.0.0 255.255.0.0 10.0.10.252 210 tag 7",
        ]
    )
    routes = {r.destination: r for r in configparse.parse(text).routes}
    assert routes["0.0.0.0/0"].interface is None
    assert routes["0.0.0.0/0"].distance == 100
    assert routes["10.20.0.0/16"].interface == "GigabitEthernet0/1"
    assert routes["10.20.0.0/16"].distance is None
    assert routes["10.30.0.0/16"].distance == 210


def test_ipv6_static_routes_are_read() -> None:
    """IPv6 の静的経路も、IPv4 と同じように経路として残す。"""
    text = "\n".join(
        [
            "hostname r1",
            "ipv6 route 2001:db8::/32 2001:db8:0:1::1",
            "ipv6 route vrf CUST 2001:db8:1::/48 GigabitEthernet0/2 2001:db8:0:2::1 200",
        ]
    )
    routes = {r.destination: r for r in configparse.parse(text).routes}
    assert routes["2001:db8::/32"].next_hop == "2001:db8:0:1::1"
    assert routes["2001:db8:1::/48"].vrf == "CUST"
    assert routes["2001:db8:1::/48"].distance == 200


def test_the_default_route_itself_can_be_the_broken_one():
    """既定経路の相手に行けないなら、逃げ道は無い。

    「既定経路があるから流れます」と言ってしまうと、実際には落ちている
    設定を軽く見せることになる。行ける既定経路だけを逃げ道と数える。
    """
    from app.services.reviewnote import HIGH

    text = "\n".join(
        [
            "hostname edge-sw-01",
            "interface Vlan10",
            " ip address 10.10.10.1 255.255.255.0",
            "ip route 0.0.0.0 0.0.0.0 192.168.99.254",
        ]
    )
    found = [f for f in policyreview.review(configparse.parse(text), text) if "次の相手" in f.title]
    assert [f.level for f in found] == [HIGH]
    assert "ここで落ちます" in found[0].why


def test_the_same_broken_route_is_only_reported_once():
    """同じことを2か所から言わない。件数が水増しされ、重さも食い違う。"""
    text = "\n".join(
        [
            "hostname edge-sw-01",
            "interface Vlan10",
            " ip address 10.10.10.1 255.255.255.0",
            "ip route 10.9.0.0 255.255.0.0 10.77.77.77",
        ]
    )
    parsed = configparse.parse(text)
    titles = [f.title for f in policyreview.review(parsed, text) if "10.77.77.77" in f.title]
    assert len(titles) == 1


def test_the_newer_vrrp_syntax_is_not_mistaken_for_another_vendor():
    """IOS の新しい VRRP は、Aruba CX と同じ書き方をする。

    これだけでベンダーを決めると、Cisco の Config がまるごと別ベンダーとして
    読まれる。マスクが落ち、冗長も読めず、そこから先の指摘がすべて外れる。
    点で区切ったマスクは AOS-CX には出てこないので、それで見分ける。
    """
    cisco = "\n".join(
        [
            "hostname r1",
            "interface Vlan20",
            " ip address 10.0.20.2 255.255.255.0",
            " vrrp 20 address-family ipv4",
            "  address 10.0.20.1 primary",
            "  priority 120",
        ]
    )
    parsed = configparse.parse(cisco)
    assert parsed.vendor is configparse.Vendor.CISCO_IOS
    assert parsed.interfaces[0].ip_address == "10.0.20.2/24"

    aruba = "\n".join(
        [
            "hostname cx-01",
            "interface vlan 20",
            "    ip address 10.0.20.2/24",
            "    vrrp 20 address-family ipv4",
            "        address 10.0.20.1 primary",
        ]
    )
    assert configparse.parse(aruba).vendor is configparse.Vendor.ARUBA_CX


def test_the_newer_vrrp_syntax_is_read_for_both_families():
    """同じ番号で ipv4 と ipv6 を別々に書ける。まとめると片方が消える。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface Vlan20",
            " ip address 10.0.20.2 255.255.255.0",
            " ipv6 address 2001:db8:0:20::2/64",
            " vrrp 20 address-family ipv4",
            "  address 10.0.20.1 primary",
            "  priority 120",
            " vrrp 20 address-family ipv6",
            "  address 2001:db8:0:20::1 primary",
        ]
    )
    parsed = configparse.parse(text)
    virtual = {r.virtual_ip for r in parsed.redundancy}
    assert virtual == {"10.0.20.1", "2001:db8:0:20::1"}
    assert {r.priority for r in parsed.redundancy} == {120, None}
    # 帯の中に収まっているので、指摘は出ない
    assert policyreview.review(parsed, text) == []


def test_a_borrowed_or_leased_address_is_not_read_as_missing() -> None:
    """DHCP・unnumbered は「付いていない」ではない。

    空欄のまま出すと、付け忘れと見分けが付かなくなる。何によって決まるかを
    残しておき、ポート表にもその形で出す。
    """
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/0",
            " ip address dhcp",
            "interface GigabitEthernet0/1",
            " ip unnumbered Loopback0",
            "interface GigabitEthernet0/2",
            " ip address 10.0.0.1 255.255.255.0",
            "interface Loopback0",
            " ip address 10.255.0.1 255.255.255.255",
        ]
    )
    found = {item.name: item for item in configparse.parse(text).interfaces}
    assert found["GigabitEthernet0/0"].ip_address is None
    assert found["GigabitEthernet0/0"].address_source == "dhcp"
    assert found["GigabitEthernet0/1"].address_source == "unnumbered Loopback0"
    assert found["GigabitEthernet0/2"].ip_address == "10.0.0.1/24"
    assert found["GigabitEthernet0/2"].address_source is None


def test_other_vendors_also_say_where_the_address_comes_from() -> None:
    forti = "\n".join(
        [
            "config system interface",
            '    edit "wan1"',
            "        set mode dhcp",
            "    next",
            "end",
        ]
    )
    assert configparse.parse(forti).interfaces[0].address_source == "dhcp"

    junos = "\n".join(
        [
            "set system host-name r1",
            "set interfaces ge-0/0/0 unit 0 family inet dhcp",
        ]
    )
    assert configparse.parse(junos).interfaces[0].address_source == "dhcp"

    yamaha = "\n".join(["# RTX1220 Rev.15.04.03", "ip lan2 address dhcp"])
    assert configparse.parse(yamaha).interfaces[0].address_source == "dhcp"
