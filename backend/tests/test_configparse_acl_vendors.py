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
    # 口（srcintf / dstintf）は条件に含める。含めないと、別の口宛ての行どうしを
    # 「上が下を覆っている」と取り違える
    assert acl.entries[0].destination == "wan1:any"
    assert acl.entries[0].source == "internal:LAN"
    # 口を書いていない行は、アドレスだけで言う
    assert acl.entries[1].source == "GUEST"


def test_a_policy_that_is_turned_off_is_not_read_as_a_rule():
    """止めたままの行を並びに混ぜると、影の判定が狂う。"""
    acl = _acl(FORTI, "firewall policy")
    assert acl.entries[1].action == "disabled"


# ---------------------------------------------------------------------------
# Juniper SRX（security policies）
# ---------------------------------------------------------------------------


def test_srx_policies_carry_their_zones():
    """SRX の許否はゾーンの組み合わせごとに並ぶ。

    条件にゾーンを入れておかないと、別のゾーン宛ての行どうしを
    「上が下を覆っている」と取り違える。
    """
    from pathlib import Path

    from app.services import configparse

    text = (Path(__file__).parent / "fixtures" / "edge-rt-01.txt").read_text()
    parsed = configparse.parse(text)
    acl = next(a for a in parsed.acls if a.name == "security policies")
    entries = {e.raw: e for e in acl.entries}

    web = entries["policy allow-web"]
    assert (web.source, web.destination) == ("untrust:any", "trust:WEB-SERVER")
    # アドレス帳の名前は、中身が分かるときだけ解決しておく
    assert web.destination_value == "trust:10.9.0.10/32"
    assert web.destination_port == "アプリ junos-https"
    assert web.log is True

    out = entries["policy trust-out"]
    assert (out.source, out.destination) == ("trust:any", "untrust:any")
    # 戻りは自動で通る機種なので、出る側が広いことを「実質全通し」と言わない
    assert acl.stateful is True


def test_srx_source_nat_is_read():
    from pathlib import Path

    from app.services import configparse

    text = (Path(__file__).parent / "fixtures" / "edge-rt-01.txt").read_text()
    parsed = configparse.parse(text)
    assert parsed.nat is not None
    rule = next(r for r in parsed.nat.rules if r.kind == "dynamic")
    assert rule.matched == "10.1.0.0/24"
    assert rule.translated == "出口のアドレス"
    assert rule.overload is True
    assert parsed.nat.outside_interfaces == ["untrust"]


def test_the_zone_of_a_port_is_written_on_the_physical_port():
    """ゾーンは論理の単位に付くが、図と台帳で見るのは物理のポート。"""
    from pathlib import Path

    from app.services import configparse

    text = (Path(__file__).parent / "fixtures" / "edge-rt-01.txt").read_text()
    parsed = configparse.parse(text)
    ports = {i.name: i for i in parsed.interfaces}
    assert "zone trust" in (ports["ge-0/0/0"].description or "")
    assert "zone untrust" in (ports["ge-0/0/1"].description or "")
    assert "ge-0/0/0.0" not in ports


def test_a_table_followed_by_config_does_not_become_neighbors():
    """show の表の下に config が続いていても、行を隣接情報にしない。"""
    from pathlib import Path

    from app.services import configparse

    text = (Path(__file__).parent / "fixtures" / "edge-rt-01.txt").read_text()
    parsed = configparse.parse(text)
    assert [n.remote_hostname for n in parsed.neighbors] == ["core-sw-01"]


def test_fortios_address_names_are_resolved_when_the_subnet_is_known():
    """名前のままでは、覆っているかどうかを言えない。"""
    text = (
        FORTI
        + """
config firewall address
    edit "LAN"
        set subnet 10.1.0.0 255.255.255.0
    next
    edit "HQ"
        set subnet 10.0.0.0/8
    next
end
"""
    )
    acl = _acl(text, "firewall policy")
    assert acl.entries[0].source_value == "internal:10.1.0.0/24"
    assert acl.entries[0].destination_value is None


def test_a_wider_row_above_on_the_same_interfaces_shadows_the_one_below():
    text = """
config system global
    set hostname "fw-01"
end
config firewall address
    edit "LAN"
        set subnet 10.1.0.0 255.255.255.0
    next
    edit "HQ"
        set subnet 10.0.0.0/8
    next
end
config firewall policy
    edit 1
        set srcintf "internal"
        set dstintf "wan1"
        set srcaddr "HQ"
        set dstaddr "all"
        set action accept
        set service "ALL"
    next
    edit 2
        set srcintf "internal"
        set dstintf "wan1"
        set srcaddr "LAN"
        set dstaddr "all"
        set action deny
        set service "ALL"
    next
    edit 3
        set srcintf "dmz"
        set dstintf "wan1"
        set srcaddr "LAN"
        set dstaddr "all"
        set action deny
        set service "ALL"
    next
end
"""
    titles = [f.title for f in policyreview.review(configparse.parse(text))]
    shadowed = [t for t in titles if "読まれません" in t]
    # 2 番は 1 番に覆われて読まれない。3 番は口が違うので覆われない
    assert len(shadowed) == 1
    assert "2" in shadowed[0]


def test_several_addresses_on_one_fortios_row_are_not_resolved_as_one():
    """2つ以上あるときは、1つ目だけで「覆っている」と言わない。"""
    text = (
        FORTI.replace('set srcaddr "LAN"', 'set srcaddr "LAN" "GUEST"')
        + """
config firewall address
    edit "LAN"
        set subnet 10.1.0.0 255.255.255.0
    next
end
"""
    )
    acl = _acl(text, "firewall policy")
    assert acl.entries[0].source == "internal:LAN ほか1件"
    assert acl.entries[0].source_value is None


def test_several_addresses_on_one_srx_policy_are_not_resolved_as_one():
    """同じ欄に2行目以降が来ても、最後の行で上書きしない。"""
    from pathlib import Path

    from app.services import configparse

    text = (Path(__file__).parent / "fixtures" / "edge-rt-01.txt").read_text() + (
        "set security policies from-zone untrust to-zone trust policy allow-web "
        "match destination-address DB-SERVER\n"
    )
    parsed = configparse.parse(text)
    acl = next(a for a in parsed.acls if a.name == "security policies")
    web = next(e for e in acl.entries if e.raw == "policy allow-web")
    assert web.destination == "trust:WEB-SERVER ほか1件"
    assert web.destination_value is None
