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


def test_fortigate_ipsec_peer_written_as_ipv6_ddns_or_dialup_is_not_missing():
    """FortiGate の IPsec の相手は remote-gw のほか remote-gw6・remotegw-ddns にも書き、
    ダイヤルアップ（type dynamic）は相手を決めない。どれも「相手が無い」とは言わない。

    出典: FortiOS 6.4.7 / 7.0.12 CLI Reference の config vpn ipsec phase1-interface
    （type [static|dynamic|...]、remote-gw6、remotegw-ddns）。
    """
    text = (
        'config system global\n    set hostname "fw-01"\nend\n'
        "config vpn ipsec phase1-interface\n"
        '    edit "v6"\n        set interface "wan1"\n'
        "        set remote-gw6 2001:db8::2\n    next\n"
        '    edit "ddns"\n        set interface "wan1"\n        set type ddns\n'
        '        set remotegw-ddns "branch.example.jp"\n    next\n'
        '    edit "dialup"\n        set interface "wan1"\n        set type dynamic\n    next\n'
        "end\n"
    )
    parsed = configparse.parse(text)
    peers = {entry.name: entry.peers for entry in parsed.crypto.maps}
    assert peers == {"v6": ["2001:db8::2"], "ddns": ["branch.example.jp"], "dialup": ["dynamic"]}
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "上がりません" in t], titles


def test_a_crypto_map_entry_that_points_to_a_dynamic_map_needs_no_peer():
    """ダイナミッククリプトマップを指す行（`ipsec-isakmp dynamic 名前`）は相手を書かない。
    「このままでは上がりません」とは言わない。

    出典: Cisco「IPsec VPN Command Reference」の crypto map (global configuration)
    （`crypto map map-name seq-num ipsec-isakmp [dynamic dynamic-map-name]`、set peer は
    dynamic crypto map では要らない）と、Cisco Secure Firewall ASA VPN CLI Configuration Guide 9.23
    「Create a Crypto Map Entry to Use the Dynamic Crypto Map」。
    """
    asa = (
        "ASA Version 9.16(4)\nhostname fw-asa\n"
        "crypto dynamic-map DYN 10 set ikev1 transform-set TS\n"
        "crypto map OUTSIDE 65535 ipsec-isakmp dynamic DYN\n"
        "crypto map OUTSIDE interface outside\n"
    )
    ios = (
        "hostname r1\n"
        "crypto dynamic-map DYN 10\n set transform-set TS\n!\n"
        "crypto map VPN 100 ipsec-isakmp dynamic DYN\n!\n"
    )
    for text in (asa, ios):
        parsed = configparse.parse(text)
        [entry] = parsed.crypto.maps
        assert entry.kind == "ipsec-isakmp dynamic", text
        assert entry.profile == "DYN", text
        titles = [f.title for f in policyreview.review(parsed)]
        assert not [t for t in titles if "上がりません" in t], titles


def test_a_huawei_ipsec_policy_built_from_a_template_needs_no_peer():
    """Huawei の `ipsec policy 名前 番号 isakmp template テンプレート` は応答側専用で、
    相手を持たない。「このままでは上がりません」とは言わない。

    出典: Huawei AR100-S〜AR3200-S V200R010 CLI-based Configuration Guide - VPN
    「Configuring an IPSec Policy Using an IPSec Policy Template」と、NetEngine AR1000V V300R021 の
    「Example for Establishing an IPSec Tunnel ... Using an IPSec Policy Template」の
    Configuration file of Router C。
    """
    text = (
        "sysname RouterC\n#\nike peer rut1\n pre-shared-key cipher %^%#xxx%^%#\n#\n"
        "ipsec proposal tran1\n esp authentication-algorithm sha2-256\n#\n"
        "ipsec policy-template use1 10\n ike-peer rut1\n proposal tran1\n#\n"
        "ipsec policy policy1 10 isakmp template use1\n#\n"
        "interface GigabitEthernet0/0/1\n ip address 60.1.3.1 255.255.255.0\n"
        " ipsec policy policy1\n#\n"
    )
    parsed = configparse.parse(text)
    [entry] = parsed.crypto.maps
    assert entry.kind == "isakmp template"
    assert entry.profile == "use1"
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "上がりません" in t], titles


def test_a_junos_ike_gateway_with_a_dynamic_peer_is_not_missing_its_peer():
    """Junos の `set security ike gateway 名前 dynamic hostname ...` は相手のアドレスを書かない形。
    「このままでは上がりません」とは言わない。

    出典: Juniper「Example: Configuring Dynamic VPN」（`set security ike gateway dyn-vpn-local-gw
    dynamic hostname dynvpn`）と Juniper Support「[SRX] Example - How to configure a dialup IPSec
    VPN with group IKE ID」（`dynamic user-at-hostname`）。
    """
    text = (
        "set system host-name srx-hq\n"
        "set security ike policy POL proposals P\n"
        "set security ike gateway GW ike-policy POL\n"
        "set security ike gateway GW dynamic hostname branch.example.jp\n"
        "set security ike gateway GW external-interface ge-0/0/0.0\n"
        "set security ipsec policy IPP proposals IP\n"
        "set security ipsec vpn VPN1 bind-interface st0.0\n"
        "set security ipsec vpn VPN1 ike gateway GW\n"
        "set security ipsec vpn VPN1 ike ipsec-policy IPP\n"
    )
    parsed = configparse.parse(text)
    [entry] = parsed.crypto.maps
    assert entry.peers == ["dynamic"]
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "上がりません" in t], titles


def test_a_disabled_fortigate_dhcp_server_is_not_counted_as_serving():
    """`set status disable` の DHCP サーバーは使われない。配っていることにしない。

    出典: FortiOS CLI Reference「config system dhcp server」（7.0.1 / 7.4.0）。
    `status` は「Enable/disable this DHCP configuration.」、既定は enable、
    disable は「Do not use this DHCP server configuration.」。
    """
    text = (
        "config system global\n    set hostname fgt\nend\n"
        "config system dhcp server\n"
        "    edit 1\n"
        '        set interface "port1"\n'
        "        set default-gateway 10.0.0.1\n"
        "        set netmask 255.255.255.0\n"
        "        set status disable\n"
        "    next\n"
        "    edit 2\n"
        '        set interface "port3"\n'
        "        set default-gateway 10.3.0.1\n"
        "        set netmask 255.255.255.0\n"
        "    next\n"
        "end\n"
    )
    pools = configparse.parse(text).dhcp.pools
    assert [p.name for p in pools] == ["dhcp 2 (port3)"]


def test_a_fortigate_exclude_range_does_not_steal_the_ip_range():
    """`config exclude-range` の `edit 1` を、DHCP サーバーの `edit` と取り違えない。

    出典: FortiOS CLI Reference「config system dhcp server」（7.4.0）。
    `config exclude-range` と `config ip-range` はどちらも `edit <id>` と
    `set start-ip` / `set end-ip` を持つ入れ子。exclude-range は配らない範囲。
    """
    text = (
        "config system global\n    set hostname fgt\nend\n"
        "config system dhcp server\n"
        "    edit 7\n"
        '        set interface "port1"\n'
        "        set default-gateway 10.0.0.1\n"
        "        set netmask 255.255.255.0\n"
        "        config exclude-range\n"
        "            edit 1\n"
        "                set start-ip 10.0.0.150\n"
        "                set end-ip 10.0.0.160\n"
        "            next\n"
        "        end\n"
        "        config ip-range\n"
        "            edit 1\n"
        "                set start-ip 10.0.0.100\n"
        "                set end-ip 10.0.0.200\n"
        "            next\n"
        "        end\n"
        "    next\n"
        "end\n"
    )
    dhcp = configparse.parse(text).dhcp
    [pool] = dhcp.pools
    assert (pool.range_start, pool.range_end) == ("10.0.0.100", "10.0.0.200")
    assert dhcp.excluded == [("10.0.0.150", "10.0.0.160")]


def test_a_fortigate_dhcp_server_with_dns_service_local_or_default_hands_out_dns():
    """`set dns-service local` / `default` は dns-server を書かない形。「配っていない」と言わない。

    出典: FortiOS CLI Reference「config system dhcp server」（7.4.0）。`dns-service` の既定は
    specify。local は「IP address of the interface the DHCP server is added to becomes the
    client's DNS server IP address.」、default は「Clients are assigned the FortiGate's
    configured DNS servers.」。
    """
    text = (
        "config system global\n    set hostname fgt\nend\n"
        "config system dns\n    set primary 192.0.2.53\n    set secondary 192.0.2.54\nend\n"
        "config system interface\n"
        '    edit "port1"\n        set ip 10.0.0.1 255.255.255.0\n    next\n'
        "end\n"
        "config system dhcp server\n"
        "    edit 1\n"
        '        set interface "port1"\n'
        "        set default-gateway 10.0.0.1\n"
        "        set netmask 255.255.255.0\n"
        "        set dns-service local\n"
        "    next\n"
        "    edit 2\n"
        '        set interface "port3"\n'
        "        set default-gateway 10.3.0.1\n"
        "        set netmask 255.255.255.0\n"
        "        set dns-service default\n"
        "    next\n"
        "end\n"
    )
    parsed = configparse.parse(text)
    assert [p.dns_servers for p in parsed.dhcp.pools] == [
        ["10.0.0.1"],
        ["192.0.2.53", "192.0.2.54"],
    ]
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "名前解決の先を配っていません" in t], titles


def test_a_cisco_dhcp_pool_with_import_all_hands_out_dns():
    """`import all` / `import interface` は DNS などを上流から受け取って配る形。
    `dns-server` を書かなくても「名前解決の先を配っていません」と言わない。

    出典: Cisco「IP Addressing Configuration Guide, Cisco IOS XE 17.x - Configuring the Cisco IOS
    XE DHCP Server」の「Configuring a Remote Device to Import DHCP Server Options from a Central
    DHCP Server」（`import {all | interface interface_name}`。「The import all command learns
    options from all the interfaces.」）。
    """
    text = (
        "hostname rt1\n"
        "ip dhcp pool LAN\n"
        " network 10.0.0.0 255.255.255.0\n"
        " default-router 10.0.0.1\n"
        " import all\n"
        "!\n"
    )
    parsed = configparse.parse(text)
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "名前解決の先を配っていません" in t], titles


def test_a_cisco_manual_binding_inherits_the_parent_pool():
    """固定割り当て（`host`）のプールは、そのアドレスを含む network のプールの設定を受け継ぐ。
    「既定ゲートウェイを配っていません」「名前解決の先を配っていません」と言わない。

    出典: Cisco「IP Addressing Configuration Guide, Cisco IOS XE 17.x - Configuring the Cisco IOS
    XE DHCP Server」の「DHCP Attribute Inheritance」（「Subnetworks inherit network parameters
    and clients inherit subnetwork parameters.」）と「Configuring Manual Bindings」
    （`host address [mask | /prefix-length]`）。
    """
    text = (
        "hostname rt1\n"
        "ip dhcp pool LAN\n"
        " network 10.0.0.0 255.255.255.0\n"
        " default-router 10.0.0.1\n"
        " dns-server 10.0.0.53\n"
        "!\n"
        "ip dhcp pool PRINTER\n"
        " host 10.0.0.20 255.255.255.0\n"
        " client-identifier 0100.1122.3344.55\n"
        "!\n"
    )
    parsed = configparse.parse(text)
    printer = next(p for p in parsed.dhcp.pools if p.name == "PRINTER")
    assert printer.default_routers == ["10.0.0.1"]
    assert printer.dns_servers == ["10.0.0.53"]
    titles = [f.title for f in policyreview.review(parsed)]
    assert not [t for t in titles if "PRINTER" in t], titles
