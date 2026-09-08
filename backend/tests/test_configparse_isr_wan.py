"""ISR の WAN 側の書き方（DMVPN・IKEv2・ゾーンベースファイアウォール）を読めること。

Catalyst の書き方だけ確かめていると、拠点ルーターで普通に使う書き方が
抜ける。ここは IOS-XE のルーターで実際に並ぶ順で置いている。
"""

from app.services import configparse

CONFIG = """\
hostname br-rt-01
Cisco IOS XE Software, Version 17.09.04a
cisco ISR4331/K9 (1RU) processor with 1695437K/6147K bytes of memory.
!
vrf definition INET
 rd 65000:100
!
crypto ikev2 proposal PROP
 encryption aes-cbc-256
 integrity sha256
 group 14
crypto ikev2 keyring KR
 peer HUB
  address 203.0.113.1
  pre-shared-key cisco123
crypto ipsec transform-set TS esp-aes 256 esp-sha256-hmac
 mode transport
crypto ipsec profile IPSEC-PROF
 set transform-set TS
!
interface Tunnel0
 description DMVPN to HUB
 ip address 172.16.0.2 255.255.255.0
 tunnel source GigabitEthernet0/0/0
 tunnel mode gre multipoint
 tunnel vrf INET
 tunnel protection ipsec profile IPSEC-PROF
!
interface GigabitEthernet0/0/0
 vrf forwarding INET
 ip address 198.51.100.2 255.255.255.252
 zone-member security OUTSIDE
!
zone security INSIDE
zone security OUTSIDE
class-map type inspect match-any CM-IN
 match protocol https
policy-map type inspect PM-IN-OUT
 class type inspect CM-IN
  inspect
zone-pair security IN-OUT source INSIDE destination OUTSIDE
 service-policy type inspect PM-IN-OUT
"""


def _parsed():
    return configparse.parse(CONFIG)


def test_the_tunnel_keeps_its_outer_vrf_and_its_ipsec_profile():
    parsed = _parsed()
    tunnel = parsed.tunnels[0]
    assert (tunnel.kind, tunnel.source, tunnel.vrf) == (
        "gre multipoint",
        "GigabitEthernet0/0/0",
        "INET",
    )
    assert parsed.crypto.applied == [("Tunnel0", "IPSEC-PROF")]


def test_the_ikev2_keyring_gives_the_peers_that_have_a_key():
    crypto = _parsed().crypto
    assert crypto.keyed_peers == ["203.0.113.1"]
    assert crypto.ike_policies[0].version == 2
    assert crypto.ike_policies[0].encryption == "aes-cbc-256"


def test_the_zone_based_firewall_is_not_mistaken_for_qos():
    """`class-map type inspect` は QoS ではなく、ゾーン間の許否。"""
    features = {(f.category, f.name) for f in _parsed().features}
    assert ("access_control", "ゾーン間ポリシー（ZBFW）") in features
    assert ("qos", "QoS") not in features
