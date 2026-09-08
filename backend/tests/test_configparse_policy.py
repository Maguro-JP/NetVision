"""ACL / NAT / DHCP / QoS / IPsec を、中身まで読めていること。

「入っている」だけを数えていたときは、書いてあるのに効いていない設定を
見つけられなかった。ここで見るのは、並びと当て先まで取り出せているか。
"""

from app.services import configparse

CONFIG = """
hostname edge-rt-01
!
interface GigabitEthernet0/0
 description To-ISP
 ip address 203.0.113.2 255.255.255.252
 ip nat outside
 ip access-group OUTSIDE-IN in
 crypto map VPNMAP
!
interface GigabitEthernet0/1
 ip address 10.10.0.1 255.255.255.0
 ip nat inside
 ip helper-address 10.20.0.10
 service-policy output SHAPE
!
ip dhcp excluded-address 10.10.0.1 10.10.0.10
ip dhcp pool OFFICE
 network 10.10.0.0 255.255.255.0
 default-router 10.10.0.1
 dns-server 10.20.0.53
 lease 0 8
!
ip access-list extended OUTSIDE-IN
 10 permit tcp any host 203.0.113.10 eq 443
 20 deny ip 10.0.0.0 0.255.255.255 any log
!
access-list 10 permit 10.10.0.0 0.0.0.255
!
ip nat pool OUTPOOL 203.0.113.20 203.0.113.30 netmask 255.255.255.0
ip nat inside source list 10 pool OUTPOOL overload
ip nat inside source static tcp 10.10.0.5 80 203.0.113.5 80
!
class-map match-any VOICE
 match dscp ef
policy-map SHAPE
 class VOICE
  priority percent 20
!
crypto isakmp policy 10
 encryption aes 256
 hash sha256
 group 14
crypto ipsec transform-set TS esp-aes 256 esp-sha256-hmac
 mode tunnel
crypto map VPNMAP 10 ipsec-isakmp
 set peer 198.51.100.1
 set transform-set TS
 set pfs group14
 match address VPN-TRAFFIC
"""


def _parsed():
    return configparse.parse(CONFIG)


def test_an_acl_keeps_the_order_it_was_written_in():
    """ACL は上から順に読まれる。並びを持たないと、影の行を見つけられない。"""
    acl = next(a for a in _parsed().acls if a.name == "OUTSIDE-IN")
    assert [entry.seq for entry in acl.entries] == ["10", "20"]
    assert acl.entries[0].action == "permit"
    assert acl.entries[0].destination == "host 203.0.113.10"
    assert acl.entries[0].destination_port == "eq 443"
    assert acl.entries[1].log is True


def test_a_numbered_standard_acl_is_read_as_a_source_only_rule():
    acl = next(a for a in _parsed().acls if a.name == "10")
    assert acl.kind == "standard"
    assert acl.entries[0].source == "10.10.0.0 0.0.0.255"
    assert acl.entries[0].destination == "any"


def test_where_an_acl_is_applied_is_kept():
    """当てていない ACL は、書いてあっても何もしていない。"""
    parsed = _parsed()
    applied = next(a for a in parsed.acls if a.name == "OUTSIDE-IN")
    assert applied.applied_to == ["interface GigabitEthernet0/0 入り"]
    referenced = next(a for a in parsed.acls if a.name == "10")
    assert "NAT" in referenced.referenced_by


def test_napt_is_told_apart_from_plain_nat():
    """overload の有無で、外から入れるかどうかが変わる。"""
    nat = _parsed().nat
    dynamic = next(rule for rule in nat.rules if rule.kind == "dynamic")
    assert dynamic.overload is True
    assert dynamic.translated == "OUTPOOL"
    assert nat.inside_interfaces == ["GigabitEthernet0/1"]
    assert nat.outside_interfaces == ["GigabitEthernet0/0"]


def test_a_static_port_translation_keeps_both_sides():
    nat = _parsed().nat
    static = next(rule for rule in nat.rules if rule.kind == "static-port")
    assert static.protocol == "tcp"
    assert static.matched == "10.10.0.5 80"
    assert static.translated == "203.0.113.5 80"


def test_a_dhcp_pool_is_read_with_what_it_hands_out():
    dhcp = _parsed().dhcp
    pool = dhcp.pools[0]
    assert pool.name == "OFFICE"
    assert pool.default_routers == ["10.10.0.1"]
    assert pool.dns_servers == ["10.20.0.53"]
    assert dhcp.excluded == [("10.10.0.1", "10.10.0.10")]
    assert dhcp.relays == [("GigabitEthernet0/1", "10.20.0.10")]


def test_qos_keeps_which_port_it_is_applied_to():
    qos = _parsed().qos
    assert qos.classes[0].name == "VOICE"
    assert qos.policies[0].classes[0].class_name == "VOICE"
    assert qos.applied == [("GigabitEthernet0/1", "output", "SHAPE")]


def test_ipsec_is_read_down_to_the_method_and_the_range():
    crypto = _parsed().crypto
    assert crypto.ike_policies[0].encryption == "aes 256"
    assert crypto.ike_policies[0].group == "14"
    assert crypto.transform_sets[0].mode == "tunnel"
    # 鍵長の数字は前の名前に付く（`256` を独立した変換にしない）
    assert crypto.transform_sets[0].transforms == ["esp-aes 256", "esp-sha256-hmac"]
    entry = crypto.maps[0]
    assert entry.peers == ["198.51.100.1"]
    assert entry.match_address == "VPN-TRAFFIC"
    assert crypto.applied == [("GigabitEthernet0/0", "VPNMAP")]


def test_the_abbreviated_encr_the_running_config_prints_is_read():
    """IOS の running-config は `encryption` を `encr` と略して印字する。"""
    text = "hostname r1\ncrypto isakmp policy 10\n encr aes 256\n hash sha256\n group 14\n"
    crypto = configparse.parse(text).crypto
    assert crypto.ike_policies[0].encryption == "aes 256"


def test_a_config_without_these_features_leaves_them_empty():
    """無いものを、読めていないだけかもしれないのに埋めない。"""
    text = "hostname sw-01\ninterface Vlan10\n ip address 10.0.0.1 255.255.255.0"
    parsed = configparse.parse(text)
    assert parsed.acls == []
    assert parsed.nat is None
    assert parsed.dhcp is None
    assert parsed.crypto is None
