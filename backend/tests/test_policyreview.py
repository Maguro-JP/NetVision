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
    item = _one(_review(text), "読まれません")
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
    assert _one(_review(text), "同じ意味の行").level == LOW


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


# ---------------------------------------------------------------------------
# ACL の行と行の重なり
# ---------------------------------------------------------------------------


def test_a_wide_rule_hides_a_narrow_one_even_when_they_look_different():
    """字面では見つからない。上が下を集合として覆っているかで見る。"""
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit tcp 10.0.0.0 0.0.0.255 any range 20 100
 20 deny tcp host 10.0.0.5 any eq www
 30 deny ip any any log
"""
    item = _one(_review(text), "20 deny tcp host 10.0.0.5 any eq www")
    assert item.level == MEDIUM
    assert "10 permit tcp 10.0.0.0 0.0.0.255 any range 20 100" in item.evidence[0]


def test_a_narrow_rule_above_a_wide_one_is_left_alone():
    """順番が正しいものまで指摘すると、直す必要の無いものを直させてしまう。"""
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 deny tcp host 10.0.0.5 any eq www
 20 permit tcp 10.0.0.0 0.0.0.255 any range 20 100
 30 deny ip any any log
"""
    assert "読まれません" not in _titles(_review(text))


def test_a_rule_we_cannot_read_is_not_called_shadowed():
    """object-group の中身はこの Config から分からない。分からないものは言わない。"""
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit ip object-group INSIDE any
 20 deny tcp host 10.0.0.5 any eq www
 30 deny ip any any log
"""
    assert "読まれません" not in _titles(_review(text))


def test_an_acl_that_stops_nothing_is_called_out():
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit tcp any host 10.0.0.1 eq 443
 20 permit ip any any
"""
    item = _one(_review(text), "実質すべて通しています")
    assert item.level == MEDIUM


def test_dropping_without_a_record_is_called_out():
    """「繋がらない」と言われたときに、ここで落ちたのかを確かめられない。"""
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit tcp any host 10.0.0.1 eq 443
 20 deny ip any any
"""
    assert _one(_review(text), "記録していません").level == LOW


def test_logging_the_drop_settles_it():
    text = """
interface GigabitEthernet0/0
 ip access-group OUT in
ip access-list extended OUT
 10 permit tcp any host 10.0.0.1 eq 443
 20 deny ip any any log
"""
    assert "記録していません" not in _titles(_review(text))


def test_an_acl_on_the_management_port_pointing_at_nothing_is_called_out():
    text = """
ip access-list standard MGMT
 permit 10.0.0.0 0.0.0.255
line vty 0 15
 transport input ssh
 access-class NOSUCH in
"""
    findings = policyreview.review(configparse.parse(text), text)
    assert _one(findings, "NOSUCH").level == HIGH


def test_not_limiting_the_management_port_is_not_called_a_mistake():
    """入る口をどこまで絞るかは現場の決めごと。間違いとは言い切れない。"""
    text = "line vty 0 15\n transport input ssh\n"
    assert policyreview.review(configparse.parse(text), text) == []


# ---------------------------------------------------------------------------
# ポリシールーティング
# ---------------------------------------------------------------------------

_PBR_BASE = """
interface GigabitEthernet0/0
 ip address 10.1.1.1 255.255.255.0
 ip policy route-map TO-WAN2
interface GigabitEthernet0/1
 ip address 192.168.1.1 255.255.255.0
ip access-list extended GUEST
 permit ip 10.1.1.0 0.0.0.255 any
"""


def _pbr(*lines: str) -> str:
    return _PBR_BASE + "route-map TO-WAN2 permit 10\n" + "".join(f" {line}\n" for line in lines)


def test_a_policy_route_whose_next_hop_is_directly_connected_is_fine():
    text = _pbr("match ip address GUEST", "set ip next-hop 192.168.1.254")
    findings = _review(text)
    assert not any("直結の帯" in f.title for f in findings)
    policy = next(p for p in configparse.parse(text).policies if p.name == "TO-WAN2")
    assert policy.kind == "policy-based-routing"
    assert policy.used_by == ["interface GigabitEthernet0/0"]


def test_a_policy_route_whose_next_hop_is_not_connected_is_called_out():
    text = (
        _PBR_BASE
        + "route-map TO-WAN2 permit 10\n match ip address GUEST\n set ip next-hop 172.16.9.1\n"
    )
    item = _one(_review(text), "直結の帯にありません")
    assert item.level == MEDIUM
    assert "172.16.9.1" in item.title


def test_a_recursive_next_hop_is_allowed_to_be_far_away():
    text = _pbr("match ip address GUEST", "set ip next-hop recursive 172.16.9.1")
    assert not any("直結の帯" in f.title for f in _review(text))


def test_a_policy_route_pointing_at_a_missing_acl_is_called_out():
    text = (
        _PBR_BASE
        + "route-map TO-WAN2 permit 10\n match ip address VIP\n set ip next-hop 192.168.1.254\n"
    )
    item = _one(_review(text), "ACL VIP がありません")
    assert item.level == HIGH


def test_a_policy_route_with_a_condition_but_no_destination_is_noted():
    text = _pbr("match ip address GUEST")
    assert _one(_review(text), "行き先を決めていません").level == LOW


def test_fortigate_router_policy_is_read_with_its_gateway():
    text = """
config system global
    set hostname fg-01
end
config system interface
    edit "wan2"
        set ip 203.0.113.2 255.255.255.248
    next
    edit "lan"
        set ip 10.1.1.1 255.255.255.0
    next
end
config router policy
    edit 1
        set input-device "lan"
        set srcaddr "GUEST"
        set gateway 203.0.113.1
        set output-device "wan2"
    next
    edit 2
        set input-device "lan"
        set gateway 198.51.100.1
        set output-device "wan2"
    next
    edit 3
        set status disable
        set gateway 1.1.1.1
    next
end
"""
    parsed = configparse.parse(text)
    pbr = [p for p in parsed.policies if p.kind == "policy-based-routing"]
    assert [p.name for p in pbr] == ["router policy 1", "router policy 2"]
    assert pbr[0].used_by == ["interface lan"]
    assert "set gateway 203.0.113.1" in pbr[0].entries[0].sets
    findings = policyreview.review(parsed)
    titles = [f.title for f in findings if "直結の帯" in f.title]
    assert titles == ["router policy 2 の次の相手 198.51.100.1 は直結の帯にありません"]


# ---------------------------------------------------------------------------
# prefix-list の並び。上から順に見て、最初に当たったところで止まる
# ---------------------------------------------------------------------------

_BGP = """router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
 neighbor 10.0.0.2 prefix-list CUST out
"""


def _prefix(*rows: str) -> str:
    lines = [f"ip prefix-list CUST seq {(i + 1) * 5} {row}" for i, row in enumerate(rows)]
    return "\n".join(lines) + "\n" + _BGP


def test_a_prefix_list_in_a_sensible_order_says_nothing():
    text = _prefix("deny 10.1.0.0/16", "permit 10.0.0.0/8 le 24")
    assert not any("読まれません" in f.title for f in _review(text))


def test_a_row_already_covered_by_an_earlier_one_is_called_out():
    text = _prefix("permit 10.0.0.0/8 le 24", "deny 10.2.0.0/16")
    item = _one(_review(text), "読まれません")
    assert item.level == MEDIUM
    assert "意図した結果になりません" in item.why


def test_a_covered_row_with_the_same_action_is_only_noted():
    text = _prefix("permit 10.0.0.0/8 le 24", "permit 10.1.0.0/16")
    assert _one(_review(text), "読まれません").level == LOW


def test_an_exact_length_row_does_not_cover_longer_ones():
    """ge も le も書かなければ、その長さちょうどにしか当たらない。"""
    text = _prefix("permit 10.0.0.0/8", "permit 10.1.0.0/16")
    assert not any("読まれません" in f.title for f in _review(text))


def test_a_row_outside_the_earlier_range_is_not_covered():
    text = _prefix("permit 10.0.0.0/8 le 16", "permit 10.1.1.0/24")
    assert not any("読まれません" in f.title for f in _review(text))


def test_a_length_range_that_matches_nothing_is_called_out():
    text = _prefix("permit 192.168.0.0/16 ge 24 le 20")
    assert _one(_review(text), "当たる長さがありません").level == MEDIUM


def test_a_ge_shorter_than_the_written_length_is_called_out():
    text = _prefix("permit 192.168.0.0/16 ge 8")
    assert _one(_review(text), "当たる長さがありません").level == MEDIUM


def test_a_route_map_pointing_at_a_missing_prefix_list_is_called_out():
    text = """ip prefix-list CUST seq 5 permit 10.0.0.0/8 le 24
route-map OUT permit 10
 match ip address prefix-list GONE
 set metric 100
route-map OUT permit 20
 match ip address prefix-list CUST
router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
 neighbor 10.0.0.2 route-map OUT out
"""
    item = _one(_review(text), "prefix-list GONE がありません")
    assert item.level == HIGH


def test_a_route_map_pointing_at_a_missing_acl_is_called_out():
    text = """ip access-list extended CUST
 permit ip 10.0.0.0 0.255.255.255 any
route-map OUT permit 10
 match ip address GONE
router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
 neighbor 10.0.0.2 route-map OUT out
"""
    assert _one(_review(text), "ACL GONE がありません").level == HIGH


def test_a_route_map_whose_tools_all_exist_says_nothing():
    text = """ip prefix-list CUST seq 5 permit 10.0.0.0/8 le 24
route-map OUT permit 10
 match ip address prefix-list CUST
router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
 neighbor 10.0.0.2 route-map OUT out
"""
    assert not any("指している" in f.title for f in _review(text))


# ---------------------------------------------------------------------------
# ポートに当てた ACL が、そもそも存在するか
# ---------------------------------------------------------------------------

_WITH_ACL = """hostname sw1
ip access-list standard MGMT
 permit 10.0.0.0 0.0.0.255
!
"""


def _review_with_text(text: str):
    """元の Config も渡す。ポートへの当て方は、読み取った形だけでは分からない。"""
    return policyreview.review(configparse.parse(text), text)


def test_an_acl_applied_to_a_port_but_never_defined_is_called_out():
    text = _WITH_ACL + "interface GigabitEthernet0/1\n ip access-group 101 in\n"
    item = _one(_review_with_text(text), "に当てた ACL 101 が")
    assert item.level == HIGH
    assert "絞れていません" in item.why


def test_an_acl_that_exists_says_nothing():
    text = _WITH_ACL + "interface GigabitEthernet0/1\n ip access-group MGMT in\n"
    assert not any("当てた ACL" in f.title for f in _review_with_text(text))


def test_nothing_is_said_when_no_acl_could_be_read_at_all():
    """読み手が対応していないだけかもしれないときは、疑わない。"""
    text = "hostname sw1\ninterface GigabitEthernet0/1\n ip access-group 101 in\n"
    assert not any("当てた ACL" in f.title for f in _review_with_text(text))


def test_the_same_missing_acl_on_one_port_is_said_once():
    text = (
        _WITH_ACL
        + "interface GigabitEthernet0/1\n ip access-group 101 in\n ip access-group 101 out\n"
    )
    assert len([f for f in _review_with_text(text) if "当てた ACL" in f.title]) == 1


def test_peer_group_settings_reach_the_neighbors_in_it() -> None:
    """peer-group に書いた内容は、そこに属する相手にも効いている。"""
    text = "\n".join(
        [
            "hostname r1",
            "router bgp 65001",
            " neighbor PEERS peer-group",
            " neighbor PEERS remote-as 65003",
            " neighbor PEERS update-source Loopback0",
            " neighbor PEERS route-map IN in",
            " neighbor 10.0.0.6 peer-group PEERS",
            " neighbor 10.0.0.7 peer-group PEERS",
            " neighbor 10.0.0.7 route-map SPECIAL in",
        ]
    )
    bgp = configparse.parse(text).bgp
    assert bgp is not None
    peers = {n.address: n for n in bgp.neighbors}
    assert peers["PEERS"].is_peer_group is True
    assert peers["10.0.0.6"].remote_as == "65003"
    assert peers["10.0.0.6"].update_source == "Loopback0"
    assert peers["10.0.0.6"].route_map_in == "IN"
    # 相手側に書いてあるほうが勝つ
    assert peers["10.0.0.7"].route_map_in == "SPECIAL"


def test_the_peer_group_line_itself_is_not_counted_as_a_neighbor() -> None:
    """雛形の行を相手として数えると、同じ指摘が余分に出る。"""
    text = "\n".join(
        [
            "hostname r1",
            "router bgp 65001",
            " neighbor PEERS peer-group",
            " neighbor PEERS remote-as 65003",
            " neighbor 10.0.0.6 peer-group PEERS",
        ]
    )
    parsed = configparse.parse(text)
    mentions = [
        finding.title
        for finding in policyreview.review(parsed)
        if "PEERS" in finding.title or any("PEERS" in line for line in finding.evidence)
    ]
    assert mentions == []


def test_the_second_address_on_a_port_also_counts_as_directly_connected() -> None:
    """secondary と IPv6 も直結の帯。落とすと「行けません」の誤りになる。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.0.10.1 255.255.255.0",
            " ip address 10.0.11.1 255.255.255.0 secondary",
            " ipv6 address 2001:db8:0:10::1/64",
            "ip route 10.20.0.0 255.255.0.0 10.0.11.9",
            "ipv6 route 2001:db8:1::/48 2001:db8:0:10::9",
            "ipv6 route 2001:db8:2::/48 2001:db8:9:9::9",
        ]
    )
    titles = [f.title for f in policyreview.review(configparse.parse(text))]
    # どの帯にも無いのはこの1本だけ
    assert titles == ["2001:db8:2::/48 の次の相手 2001:db8:9:9::9 へ行けません"]


def test_a_virtual_address_may_sit_on_the_second_band_of_a_port() -> None:
    """secondary の帯を使った冗長を、帯の外だと言ってしまわない。"""
    text = "\n".join(
        [
            "hostname sw1",
            "interface Vlan20",
            " ip address 10.0.20.2 255.255.255.0",
            " ip address 10.0.21.2 255.255.255.0 secondary",
            " standby 20 ip 10.0.20.254",
            " standby 21 ip 10.0.21.254",
        ]
    )
    assert policyreview.review(configparse.parse(text)) == []


def test_the_virtual_address_checks_still_fire() -> None:
    """帯の外・自分と同じ・配れないアドレスは、これまで通り言う。"""
    text = "\n".join(
        [
            "hostname sw1",
            "interface Vlan30",
            " ip address 10.0.30.2 255.255.255.0",
            " standby 30 ip 10.0.99.254",
            " standby 31 ip 10.0.30.2",
            " standby 32 ip 10.0.30.255",
        ]
    )
    titles = [f.title for f in policyreview.review(configparse.parse(text))]
    assert len(titles) == 3
    assert all(f.level == HIGH for f in policyreview.review(configparse.parse(text)))


def _review_titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text))]


def test_two_ports_holding_the_same_band_are_pointed_out() -> None:
    """同じ帯を2つのポートに付けると、どちらから出すかが決まらない。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.0.10.1 255.255.255.0",
            "interface GigabitEthernet0/2",
            " ip address 10.0.10.9 255.255.255.0",
        ]
    )
    assert _review_titles(text) == ["GigabitEthernet0/1 と GigabitEthernet0/2 の帯が重なっています"]


def test_a_band_inside_a_wider_one_also_counts_as_overlapping() -> None:
    """片方がもう片方を覆っている形も、重なりには違いない。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.0.0.1 255.255.0.0",
            "interface GigabitEthernet0/2",
            " ip address 10.0.5.1 255.255.255.0",
        ]
    )
    assert len(_review_titles(text)) == 1


def test_bands_split_by_vrf_or_shut_down_are_not_pointed_out() -> None:
    """VRF が違えば分かれている。止めてあるポートは、その帯を持っていない。"""
    by_vrf = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.0.10.1 255.255.255.0",
            "interface GigabitEthernet0/2",
            " vrf forwarding CUST",
            " ip address 10.0.10.1 255.255.255.0",
        ]
    )
    shut = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.0.10.1 255.255.255.0",
            "interface GigabitEthernet0/2",
            " ip address 10.0.10.9 255.255.255.0",
            " shutdown",
        ]
    )
    assert _review_titles(by_vrf) == []
    assert _review_titles(shut) == []


def test_the_first_and_last_address_of_a_band_cannot_sit_on_a_port() -> None:
    """帯の先頭と末尾は、ポートにも端末にも付けられない。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface Vlan10",
            " ip address 10.0.10.0 255.255.255.0",
            "interface Vlan20",
            " ip address 10.0.20.255 255.255.255.0",
        ]
    )
    findings = policyreview.review(configparse.parse(text))
    assert [f.level for f in findings] == [HIGH, HIGH]
    # 使える範囲まで書いて、直し方が分かるようにしている
    assert "10.0.10.1 から 10.0.10.254" in findings[0].why


def test_point_to_point_and_loopback_addresses_are_left_alone() -> None:
    """/31 は両端とも使え、/32 は帯ではない。IPv6 はここでは見ていない。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " ip address 10.1.1.0 255.255.255.254",
            "interface Loopback0",
            " ip address 1.1.1.1 255.255.255.255",
            "interface GigabitEthernet0/2",
            " ipv6 address 2001:db8::/64",
        ]
    )
    assert policyreview.review(configparse.parse(text)) == []


def test_the_same_address_on_two_ports_is_pointed_out() -> None:
    """帯の重なりとは別の話で、ホスト1つ分（/32）でも起きる。

    片方は上がらないか、後から入れたほうが弾かれる。どちらにしても、
    片方は意図した通りに動かない。
    """
    text = "\n".join(
        [
            "hostname r1",
            "interface Loopback0",
            " ip address 1.1.1.1 255.255.255.255",
            "interface Loopback1",
            " ip address 1.1.1.1 255.255.255.255",
        ]
    )
    findings = policyreview.review(configparse.parse(text))
    assert [f.level for f in findings] == [HIGH]
    assert "1.1.1.1 が、Loopback0 と Loopback1 の両方に付いています" == findings[0].title


def test_the_same_thing_is_not_said_twice() -> None:
    """アドレスが同じなら、帯が重なっているのは当たり前である。

    2つの言葉で2回言うと、読む側は問題の数を数え違える。
    """
    same = "\n".join(
        [
            "hostname r1",
            "interface Vlan10",
            " ip address 10.0.10.1 255.255.255.0",
            "interface Vlan11",
            " ip address 10.0.10.1 255.255.255.0",
        ]
    )
    titles = [f.title for f in policyreview.review(configparse.parse(same))]
    assert len(titles) == 1
    assert "両方に付いています" in titles[0]

    # アドレスが違って帯だけ重なるときは、これまで通り帯の話をする
    overlapping = "\n".join(
        [
            "hostname r1",
            "interface Vlan10",
            " ip address 10.0.10.1 255.255.255.0",
            "interface Vlan11",
            " ip address 10.0.10.9 255.255.255.0",
        ]
    )
    titles = [f.title for f in policyreview.review(configparse.parse(overlapping))]
    assert titles == ["Vlan10 と Vlan11 の帯が重なっています"]


def test_the_same_address_in_different_vrfs_is_fine() -> None:
    """VRF が違えば分かれている。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface GigabitEthernet0/1",
            " vrf forwarding A",
            " ip address 10.0.0.1 255.255.255.0",
            "interface GigabitEthernet0/2",
            " vrf forwarding B",
            " ip address 10.0.0.1 255.255.255.0",
        ]
    )
    assert policyreview.review(configparse.parse(text)) == []


def _protocol_config(body: str) -> str:
    return "hostname r1\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.0\n" + body


def test_isis_without_a_net_address_is_pointed_out() -> None:
    # NET が無いと自分の名前が決まらず、隣とつながらない
    parsed = configparse.parse(_protocol_config("router isis\n is-type level-2-only\n"))
    findings = policyreview.review(parsed)
    assert any("NET アドレス" in item.title for item in findings)


def test_rip_without_a_version_is_pointed_out() -> None:
    parsed = configparse.parse(_protocol_config("router rip\n network 10.0.0.0\n"))
    assert any("版が書かれていません" in item.title for item in policyreview.review(parsed))


def test_eigrp_split_across_two_numbers_is_pointed_out() -> None:
    body = "router eigrp 100\n network 10.0.0.0\nrouter eigrp 200\n network 10.1.0.0\n"
    parsed = configparse.parse(_protocol_config(body))
    assert any("2 つの番号" in item.title for item in policyreview.review(parsed))


def test_redistribution_without_a_filter_is_pointed_out() -> None:
    body = "router eigrp 100\n network 10.0.0.0\n redistribute static\n"
    parsed = configparse.parse(_protocol_config(body))
    assert any("絞り込みなし" in item.title for item in policyreview.review(parsed))


def test_a_tidy_protocol_block_says_nothing() -> None:
    # 誤指摘を出さないこと。書いてある事実だけで言えるものに限っている
    body = (
        "router eigrp 100\n network 10.0.0.0\n redistribute static route-map TO-EIGRP\n"
        "router rip\n version 2\n network 10.0.0.0\n"
        "router isis\n net 49.0001.0000.0000.0001.00\n"
        "route-map TO-EIGRP permit 10\n"
    )
    parsed = configparse.parse(_protocol_config(body))
    assert policyreview.review(parsed) == []
