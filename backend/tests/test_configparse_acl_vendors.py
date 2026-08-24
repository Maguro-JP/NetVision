"""通信の制限を、ベンダーをまたいで同じ器で読めること。

書き方はまるで違うが、「上から順に見て、最初に当たった行で決まる」は同じ。
同じ器に入っていれば、影の行を見つける仕組みは1つで足りる。ここで見るのは、
どのベンダーでも中身と当て先が取れているか。
"""

from app.services import configparse, policyreview

QX = """
sysname qx-sw-01
#
acl number 3000 name INTERNET
 rule 5 permit tcp source 10.0.0.0 0.0.0.255 destination-port eq 80
 rule 10 deny tcp source 10.0.0.5 0.0.0.0 destination-port eq 80
 rule 15 deny ip logging
#
interface GigabitEthernet1/0/1
 packet-filter 3000 inbound
"""

IX = """
hostname ix-rt-01
ip access-list SEC-IN permit tcp src 10.0.0.0/24 dest any dport 443
ip access-list SEC-IN deny ip src any dest any
interface GigaEthernet0.1
 ip filter SEC-IN 1 in
"""

JUNOS = """
set system host-name junos-rt-01
set firewall family inet filter PROTECT term ALLOW from source-address 10.0.0.0/24
set firewall family inet filter PROTECT term ALLOW from protocol tcp
set firewall family inet filter PROTECT term ALLOW from destination-port 22
set firewall family inet filter PROTECT term ALLOW then accept
set firewall family inet filter PROTECT term DROP then discard
set interfaces ge-0/0/0 unit 0 family inet filter input PROTECT
"""

FORTI = """
config system global
    set hostname "fw-01"
end
config firewall policy
    edit 1
        set name "to-internet"
        set srcintf "internal"
        set dstintf "wan1"
        set srcaddr "LAN"
        set dstaddr "all"
        set action accept
        set service "HTTPS"
    next
    edit 2
        set name "block-guest"
        set srcaddr "GUEST"
        set dstaddr "all"
        set action deny
        set status disable
    next
end
"""


def _acl(text: str, name: str):
    parsed = configparse.parse(text)
    return next(acl for acl in parsed.acls if acl.name == name)


def test_comware_rules_are_read_by_the_words_not_the_positions():
    acl = _acl(QX, "3000")
    assert [entry.seq for entry in acl.entries] == ["5", "10", "15"]
    first = acl.entries[0]
    assert first.protocol == "tcp"
    assert first.source == "10.0.0.0 0.0.0.255"
    assert first.destination == "any"  # 書いていなければ、どこへでも当たる
    assert first.destination_port == "eq 80"
    assert acl.entries[2].log is True
    assert acl.applied_to == ["interface GigabitEthernet1/0/1 入り"]


def test_a_shadowed_rule_is_found_on_comware_too():
    """器が同じなら、見つける仕組みは1つで足りる。"""
    findings = policyreview.review(configparse.parse(QX))
    assert any("読まれません" in f.title for f in findings)


def test_ix_rules_are_read_with_where_they_are_applied():
    acl = _acl(IX, "SEC-IN")
    assert acl.entries[0].source == "10.0.0.0/24"
    assert acl.entries[0].destination_port == "eq 443"
    assert acl.entries[1].action == "deny"
    assert acl.applied_to == ["interface GigaEthernet0.1 入り"]


def test_a_junos_term_is_one_rule_even_though_it_is_written_over_several_lines():
    acl = _acl(JUNOS, "PROTECT")
    allow = acl.entries[0]
    assert allow.seq == "ALLOW"
    assert allow.action == "permit"
    assert allow.source == "10.0.0.0/24"
    assert allow.destination_port == "eq 22"
    assert acl.entries[1].action == "deny"
    assert acl.applied_to == ["interface ge-0/0/0.0 入り"]


def test_a_fortios_policy_table_is_read_as_rules_in_order():
    acl = _acl(FORTI, "firewall policy")
    assert acl.kind == "policy"
    assert acl.applied_to == ["ポリシー表"]  # ポリシー表は、書いた時点で効いている
    assert acl.entries[0].action == "permit"
    assert acl.entries[0].destination == "any"  # all は、どこへでも
    assert acl.entries[0].source == "LAN"


def test_a_policy_that_is_turned_off_is_not_read_as_a_rule():
    """止めたままの行を並びに混ぜると、影の判定が狂う。"""
    acl = _acl(FORTI, "firewall policy")
    assert acl.entries[1].action == "disabled"
