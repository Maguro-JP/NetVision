"""NAT / DHCP / QoS / IPsec を、Cisco 以外の書き方でも読む。

書き方は違っても、決めていることは同じ（何が何に変わるか、どこからどこまで
配るか、誰とどの方式で暗号化するか）。同じ器に入れておけば、粗を見るところは
1つで足りる。ここでは「読めていること」と「そのまま同じ目で見られること」を
確かめる。
"""

from app.services import configparse, policyreview

FORTIGATE = """
#config-version=FGT100F-7.2.8-FW-build1639-230926:opmode=0
config system global
    set hostname "fw-osaka"
end
config firewall address
    edit "LAN"
        set subnet 10.0.0.0 255.255.255.0
    next
end
config firewall ippool
    edit "pool-out"
        set startip 203.0.113.20
        set endip 203.0.113.25
    next
end
config firewall policy
    edit 1
        set name "to-internet"
        set srcintf "port1"
        set dstintf "port2"
        set srcaddr "LAN"
        set dstaddr "all"
        set action accept
        set nat enable
        set poolname "pool-out"
    next
end
config firewall vip
    edit "web-in"
        set extip 203.0.113.10
        set mappedip "10.0.0.10"
        set extintf "port2"
        set portforward enable
        set protocol tcp
    next
end
config system dhcp server
    edit 1
        set interface "port1"
        set default-gateway 10.0.0.1
        set netmask 255.255.255.0
        set dns-server1 10.0.0.53
        config ip-range
            edit 1
                set start-ip 10.0.0.100
                set end-ip 10.0.0.200
            next
        end
    next
end
config firewall shaper traffic-shaper
    edit "voice"
        set guaranteed-bandwidth 2000
        set maximum-bandwidth 5000
        set priority high
    next
end
config firewall shaping-policy
    edit 1
        set name "voice-out"
        set service "SIP"
        set dstintf "port2"
        set traffic-shaper "voice"
    next
end
config vpn ipsec phase1-interface
    edit "to-hq"
        set interface "port2"
        set ike-version 2
        set proposal aes256-sha256
        set dhgrp 14
        set remote-gw 198.51.100.1
        set psksecret ENC xxxx
    next
end
config vpn ipsec phase2-interface
    edit "to-hq-p2"
        set phase1name "to-hq"
        set proposal aes256-sha256
        set src-subnet 10.0.0.0 255.255.255.0
        set dst-subnet 10.1.0.0 255.255.255.0
    next
end
"""

JUNOS = """
set system host-name srx-hq
set security nat source pool out-pool address 203.0.113.30
set security nat source rule-set trust-to-untrust from zone trust
set security nat source rule-set trust-to-untrust to zone untrust
set security nat source rule-set trust-to-untrust rule any match source-address 10.2.0.0/24
set security nat source rule-set trust-to-untrust rule any then source-nat pool out-pool
set access address-assignment pool office family inet network 10.2.0.0/24
set access address-assignment pool office family inet range r1 low 10.2.0.100
set access address-assignment pool office family inet range r1 high 10.2.0.200
set access address-assignment pool office family inet dhcp-attributes router 10.2.0.1
set access address-assignment pool office family inet dhcp-attributes name-server 10.2.0.53
set class-of-service classifiers dscp c1 forwarding-class expedited-forwarding code-points 101110
set class-of-service schedulers voice transmit-rate percent 30
set class-of-service schedulers voice priority strict-high
set class-of-service scheduler-maps sm1 forwarding-class expedited-forwarding scheduler voice
set class-of-service interfaces ge-0/0/0 scheduler-map sm1
set security ike proposal ike-prop authentication-method pre-shared-keys
set security ike proposal ike-prop dh-group group14
set security ike proposal ike-prop encryption-algorithm aes-256-cbc
set security ike proposal ike-prop authentication-algorithm sha-256
set security ike gateway gw-hq address 198.51.100.9
set security ike gateway gw-hq external-interface ge-0/0/0
set security ipsec proposal ipsec-prop encryption-algorithm aes-256-cbc
set security ipsec proposal ipsec-prop authentication-algorithm hmac-sha-256-128
set security ipsec vpn vpn-hq ike gateway gw-hq
set security ipsec vpn vpn-hq ike ipsec-policy ipsec-prop
set security ipsec vpn vpn-hq bind-interface st0.0
"""


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text))]


# ---------------------------------------------------------------------------
# FortiGate
# ---------------------------------------------------------------------------


def test_fortigate_nat_is_read_from_the_three_places_it_is_written():
    parsed = configparse.parse(FORTIGATE)
    assert parsed.nat is not None
    kinds = {(rule.kind, rule.matched, rule.translated, rule.overload) for rule in parsed.nat.rules}
    # 出る側。ポリシーの set nat enable と ippool
    assert ("dynamic", "LAN", "pool-out", True) in kinds
    # 入る側。VIP は外の1つを中の1台へ
    assert ("static-port", "203.0.113.10", "10.0.0.10", False) in kinds
    assert parsed.nat.inside_interfaces == ["port1"]
    assert "port2" in parsed.nat.outside_interfaces


def test_fortigate_dhcp_keeps_the_range_so_the_gateway_is_not_misjudged():
    """配る範囲をそのまま書く機種では、範囲の外は配られない。"""
    pool = configparse.parse(FORTIGATE).dhcp.pools[0]
    assert (pool.range_start, pool.range_end) == ("10.0.0.100", "10.0.0.200")
    assert pool.default_routers == ["10.0.0.1"]
    # 10.0.0.1 は範囲の外なので、「ゲートウェイまで配る」とは言わない
    assert not [t for t in titles(FORTIGATE) if "ゲートウェイのアドレスも配って" in t]


def test_fortigate_ipsec_is_read_as_the_same_three_things():
    crypto = configparse.parse(FORTIGATE).crypto
    assert [(p.version, p.encryption, p.hash, p.group) for p in crypto.ike_policies] == [
        (2, "aes256", "sha256", "14")
    ]
    entry = crypto.maps[0]
    assert entry.peers == ["198.51.100.1"]
    assert entry.transform_sets == ["to-hq-p2"]
    # phase1 は出口のポートに結ばれている。当たっていない扱いにはしない
    assert ("port2", "to-hq") in crypto.applied


def test_a_clean_fortigate_config_gets_no_findings():
    """読み替えが雑だと、正しい設定にまで指摘が出る。そこを止める。"""
    assert titles(FORTIGATE) == []


def test_fortigate_reports_a_name_that_is_not_defined():
    text = FORTIGATE.replace(
        '    edit "LAN"\n        set subnet 10.0.0.0 255.255.255.0\n    next\n', ""
    )
    assert "NAT が指している LAN が、この Config にありません" in titles(text)


def test_fortigate_reports_an_old_method():
    text = FORTIGATE.replace(
        "set proposal aes256-sha256\n        set dhgrp 14",
        "set proposal des-md5\n        set dhgrp 2",
    )
    found = " ".join(titles(text))
    assert "des" in found and "md5" in found


def test_fortigate_shaper_is_read_with_what_uses_it():
    """型（shaper）と当て先（shaping-policy）を、Cisco と同じ形へ読み替える。"""
    qos = configparse.parse(FORTIGATE).qos
    assert qos is not None
    voice = next(p for p in qos.policies if p.name == "voice")
    assert [a.class_name for a in voice.classes] == ["voice-out"]
    assert "上限 5000 kbps" in voice.classes[0].actions
    assert ("port2", "output", "voice") in qos.applied


def test_fortigate_shaper_that_nothing_uses_is_reported():
    text = FORTIGATE.replace('        set traffic-shaper "voice"\n', "")
    assert "優先制御の voice は、どのポートにも当たっていません" in titles(text)


# ---------------------------------------------------------------------------
# Junos
# ---------------------------------------------------------------------------


def test_junos_nat_is_read_with_its_direction():
    parsed = configparse.parse(JUNOS)
    assert parsed.nat is not None
    rule = parsed.nat.rules[0]
    assert (rule.kind, rule.direction, rule.matched, rule.translated) == (
        "dynamic",
        "inside",
        "10.2.0.0/24",
        "out-pool",
    )
    assert rule.overload is True
    assert parsed.nat.inside_interfaces == ["trust"]
    assert parsed.nat.outside_interfaces == ["untrust"]


def test_junos_dhcp_is_read_with_the_range():
    pool = configparse.parse(JUNOS).dhcp.pools[0]
    assert pool.network == "10.2.0.0/24"
    assert (pool.range_start, pool.range_end) == ("10.2.0.100", "10.2.0.200")
    assert pool.default_routers == ["10.2.0.1"]
    assert pool.dns_servers == ["10.2.0.53"]


def test_junos_ipsec_is_tied_back_to_its_gateway_and_interface():
    crypto = configparse.parse(JUNOS).crypto
    entry = crypto.maps[0]
    assert entry.name == "vpn-hq"
    assert entry.peers == ["198.51.100.9"]
    assert entry.transform_sets == ["ipsec-prop"]
    assert ("ge-0/0/0", "vpn-hq") in crypto.applied


def test_a_clean_junos_config_gets_no_findings():
    assert titles(JUNOS) == []


def test_junos_cos_is_read_as_the_same_three_things():
    qos = configparse.parse(JUNOS).qos
    assert qos is not None
    policy = next(p for p in qos.policies if p.name == "sm1")
    action = policy.classes[0]
    assert action.class_name == "expedited-forwarding"
    # scheduler の中身（出し方）まで、その仕分けの行に添える
    assert "transmit-rate percent 30" in action.actions
    assert ("ge-0/0/0", "output", "sm1") in qos.applied


def test_junos_scheduler_map_that_is_not_applied_is_reported():
    text = JUNOS.replace("set class-of-service interfaces ge-0/0/0 scheduler-map sm1\n", "")
    assert "優先制御の sm1 は、どのポートにも当たっていません" in titles(text)


def test_junos_reports_a_weak_group():
    text = JUNOS.replace("dh-group group14", "dh-group group2")
    assert [t for t in titles(text) if "group" in t or "群" in t]


def test_fortigate_reports_an_old_group_even_when_it_is_not_the_first():
    """`set dhgrp 14 5` のように並べる書き方。1つでも古ければ言う。"""
    text = FORTIGATE.replace("set dhgrp 14", "set dhgrp 14 5")
    assert any("鍵の作り方が古い" in t for t in titles(text))
    assert not any("鍵の作り方が古い" in t for t in titles(FORTIGATE))


def test_a_vip_nobody_points_at_is_mentioned_when_the_text_is_available():
    """VIP は作っただけでは通らない。ポリシーの宛先に置いてはじめて外から届く。"""
    parsed = configparse.parse(FORTIGATE)
    found = [f.title for f in policyreview.review(parsed, FORTIGATE)]
    assert "VIP web-in は、どのポリシーからも指されていません" in found

    used = FORTIGATE.replace('set dstaddr "all"', 'set dstaddr "web-in"', 1)
    found = [f.title for f in policyreview.review(configparse.parse(used), used)]
    assert not [t for t in found if "VIP web-in" in t]
    # 元の Config が無いときは、グループ越しの参照を確かめられないので言わない
    assert not [t for t in titles(FORTIGATE) if "VIP web-in" in t]
