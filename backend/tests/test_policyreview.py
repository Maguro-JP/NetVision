"""書いたものが効いているかどうかまで言えること。

Config を書く道具である以上、「入っている」だけでは足りない。当てていない
ACL、overload の無い NAT、上がらない crypto map は、書いた人が気づかない。
ここで見るのは、動かさなくても言える粗を、根拠つきで挙げられているか。
"""

from app.services import configparse, policyreview
from app.services.reviewnote import HIGH, LOW, MEDIUM


def _review(text: str):
    return policyreview.review(configparse.parse(text))


def _titles(findings) -> str:
    return " / ".join(f.title for f in findings)


def _one(findings, word: str):
    return next(f for f in findings if word in f.title)


# ---------------------------------------------------------------------------
# BGP
# ---------------------------------------------------------------------------

EBGP = """
router bgp 65001
 neighbor 203.0.113.1 remote-as 65100
"""


def test_an_external_peer_without_an_outbound_filter_is_called_out():
    """絞っていないと、自分の回線が他所どうしの通り道になる。"""
    item = _one(_review(EBGP), "出す経路を絞って")
    assert item.level == HIGH
    assert "203.0.113.1" in item.evidence[0]


def test_an_external_peer_without_a_prefix_limit_is_called_out():
    assert _one(_review(EBGP), "経路の数に上限").level == MEDIUM


def test_an_internal_peer_is_not_asked_for_those():
    """中の相手にまで同じことを言うと、読まれなくなる。"""
    text = (
        "router bgp 65001\n neighbor 10.0.0.2 remote-as 65001\n neighbor 10.0.0.2 next-hop-self\n"
    )
    findings = _review(text)
    assert "出す経路を絞って" not in _titles(findings)
    assert "経路の数に上限" not in _titles(findings)


def test_a_peer_pointing_at_a_route_map_that_does_not_exist_is_called_out():
    text = EBGP + " neighbor 203.0.113.1 route-map MISSING out\n"
    item = _one(_review(text), "MISSING")
    assert item.level == HIGH


def test_a_loopback_peering_without_multihop_will_not_come_up():
    text = EBGP + " neighbor 203.0.113.1 update-source Loopback0\n"
    item = _one(_review(text), "接続が張れません")
    assert "ebgp-multihop" in item.why


def test_external_routes_that_the_inside_cannot_use_are_called_out():
    text = EBGP + " neighbor 10.0.0.2 remote-as 65001\n"
    item = _one(_review(text), "中の相手から使えない")
    assert "10.0.0.2" in item.why


# ---------------------------------------------------------------------------
# route-map / ACL
# ---------------------------------------------------------------------------


def test_a_line_that_lets_everything_through_hides_the_lines_below_it():
    """ACL は最初に当たった行で決まる。下の行は読まれない。"""
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit ip any any
 20 deny tcp any any eq 23
"""
    item = _one(_review(text), "途中で全部決めています")
    assert item.level == MEDIUM
    assert "20 deny tcp any any eq 23" in item.evidence


def test_an_acl_nobody_applies_is_called_out():
    text = "ip access-list extended LEFTOVER\n permit ip any any\n"
    assert _one(_review(text), "どこにも当たっていません").level == MEDIUM


def test_an_applied_acl_with_no_permit_stops_everything():
    text = """
interface GigabitEthernet0/0
 ip access-group BLOCK in
ip access-list extended BLOCK
 10 deny tcp any any eq 23
"""
    item = _one(_review(text), "全部落とします")
    assert item.level == HIGH
    assert "GigabitEthernet0/0" in item.why


def test_the_same_rule_written_twice_is_pointed_out():
    text = """
interface GigabitEthernet0/0
 ip access-group DUP in
ip access-list extended DUP
 10 permit tcp any host 10.0.0.1 eq 443
 20 permit tcp any host 10.0.0.1 eq 443
"""
    assert _one(_review(text), "同じ条件の行").level == LOW


def test_a_route_map_that_denies_everything_is_called_out():
    text = """
route-map BLOCK deny 10
 match ip address prefix-list ANY
router bgp 65001
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.0.0.2 route-map BLOCK out
"""
    assert _one(_review(text), "通す行がありません").level == HIGH


def test_an_unconditional_permit_in_the_middle_of_a_route_map_is_called_out():
    text = """
route-map SET permit 10
 set metric 100
route-map SET permit 20
 match ip address prefix-list CUST
 set local-preference 200
router bgp 65001
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.0.0.2 route-map SET out
"""
    item = _one(_review(text), "途中で全部通しています")
    assert "読まれません" in item.why


# ---------------------------------------------------------------------------
# NAT / NAPT
# ---------------------------------------------------------------------------

NAT_BASE = """
interface GigabitEthernet0/0
 ip nat outside
interface GigabitEthernet0/1
 ip nat inside
ip access-list extended NATLIST
 permit ip 10.0.0.0 0.0.0.255 any
"""


def test_a_dynamic_translation_without_overload_runs_out_of_addresses():
    text = NAT_BASE + "ip nat inside source list NATLIST interface GigabitEthernet0/0\n"
    item = _one(_review(text), "変換後のアドレスが足りません")
    assert item.level == HIGH
    assert "NAPT" in item.why


def test_overload_is_left_alone():
    text = NAT_BASE + "ip nat inside source list NATLIST interface GigabitEthernet0/0 overload\n"
    assert "変換後のアドレスが足りません" not in _titles(_review(text))


def test_translation_with_only_one_side_marked_does_nothing():
    text = """
interface GigabitEthernet0/1
 ip nat inside
ip access-list extended NATLIST
 permit ip 10.0.0.0 0.0.0.255 any
ip nat inside source list NATLIST interface GigabitEthernet0/0 overload
"""
    item = _one(_review(text), "ポートが決まっていません")
    assert item.level == HIGH


def test_a_translation_pointing_at_a_missing_acl_is_called_out():
    text = NAT_BASE + "ip nat inside source list NOSUCH interface GigabitEthernet0/0 overload\n"
    assert _one(_review(text), "NOSUCH").level == HIGH


def test_the_same_outside_address_used_twice_is_called_out():
    text = NAT_BASE + (
        "ip nat inside source static 10.0.0.5 203.0.113.5\n"
        "ip nat inside source static 10.0.0.6 203.0.113.5\n"
    )
    assert _one(_review(text), "2か所で使っています").level == MEDIUM


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------


def test_a_pool_without_a_gateway_looks_fine_but_cannot_leave_the_segment():
    text = "ip dhcp pool OFFICE\n network 10.0.0.0 255.255.255.0\n dns-server 10.0.0.53\n"
    item = _one(_review(text), "既定ゲートウェイを配って")
    assert item.level == HIGH


def test_a_gateway_outside_the_range_is_called_out():
    text = (
        "ip dhcp pool OFFICE\n network 10.0.0.0 255.255.255.0\n"
        " default-router 10.9.9.1\n dns-server 10.0.0.53\n"
    )
    assert _one(_review(text), "配る範囲の外").level == HIGH


def test_handing_out_the_gateway_address_itself_is_called_out():
    text = (
        "ip dhcp pool OFFICE\n network 10.0.0.0 255.255.255.0\n"
        " default-router 10.0.0.1\n dns-server 10.0.0.53\n"
    )
    item = _one(_review(text), "ゲートウェイのアドレスも配って")
    assert item.level == MEDIUM


def test_excluding_the_gateway_settles_it():
    text = (
        "ip dhcp excluded-address 10.0.0.1 10.0.0.10\n"
        "ip dhcp pool OFFICE\n network 10.0.0.0 255.255.255.0\n"
        " default-router 10.0.0.1\n dns-server 10.0.0.53\n"
    )
    assert _review(text) == []


# ---------------------------------------------------------------------------
# QoS
# ---------------------------------------------------------------------------


def test_a_policy_nobody_applies_does_not_change_any_order():
    text = (
        "class-map match-any VOICE\n match dscp ef\n"
        "policy-map SHAPE\n class VOICE\n  priority percent 20\n"
    )
    assert _one(_review(text), "どのポートにも当たっていません").level == MEDIUM


def test_a_policy_pointing_at_a_missing_class_is_called_out():
    text = """
interface GigabitEthernet0/0
 service-policy output SHAPE
policy-map SHAPE
 class DATA
  bandwidth percent 40
"""
    assert _one(_review(text), "class-map DATA").level == HIGH


# ---------------------------------------------------------------------------
# IPsec
# ---------------------------------------------------------------------------

VPN = """
interface GigabitEthernet0/0
 crypto map VPNMAP
ip access-list extended VPN-TRAFFIC
 permit ip 10.0.0.0 0.0.0.255 10.1.0.0 0.0.0.255
crypto ipsec transform-set TS esp-aes 256 esp-sha256-hmac
crypto map VPNMAP 10 ipsec-isakmp
 set peer 198.51.100.1
 set transform-set TS
 set pfs group14
 match address VPN-TRAFFIC
"""


def test_a_broken_method_is_high():
    text = VPN + "crypto isakmp policy 10\n encryption des\n hash md5\n group 2\n"
    assert _one(_review(text), "破られている方式").level == HIGH
    assert _one(_review(text), "鍵の作り方が古い").level == MEDIUM


def test_a_current_method_is_left_alone():
    text = VPN + "crypto isakmp policy 10\n encryption aes 256\n hash sha256\n group 14\n"
    assert _review(text) == []


def test_a_crypto_map_nobody_applies_leaves_the_traffic_in_the_clear():
    text = VPN.replace(" crypto map VPNMAP\n", "")
    item = _one(_review(text), "どのポートにも当たっていません")
    assert item.level == HIGH
    assert "そのまま流れます" in item.why


def test_a_crypto_map_missing_a_piece_will_not_come_up():
    text = """
interface GigabitEthernet0/0
 crypto map VPNMAP
crypto map VPNMAP 10 ipsec-isakmp
 set peer 198.51.100.1
"""
    item = _one(_review(text), "上がりません")
    assert "方式" in item.why and "範囲" in item.why


def test_every_finding_says_why_and_what_it_looked_at():
    """理由の無い指摘は直しようが無い。"""
    findings = _review(EBGP + VPN + NAT_BASE)
    assert findings
    for item in findings:
        assert item.why and item.evidence
        assert item.level in (HIGH, MEDIUM, LOW)
