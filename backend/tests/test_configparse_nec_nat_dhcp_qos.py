"""NEC（IX / QX）の変換・配布・優先制御を読む。

IX は自社系、QX は Comware ベースで書き方がまったく違う。どちらも
Cisco と同じ器へ読み替えて、粗を見るところは1つのままにする。
IX の優先制御は、確かめられる書き方が手元に無いので読んでいない。
読めないものを当てずっぽうで読むと、間違った断定が資料に載る。
"""

from app.services import configparse, policyreview

QX = """
#
 sysname qx-sw-osaka
#
 version 7.1.070, Release 1234
#
nat address-group 1
 address 203.0.113.20 203.0.113.25
#
acl number 3000
 rule 5 permit ip source 10.5.0.0 0.0.0.255
#
dhcp server ip-pool lan
 network 10.5.0.0 mask 255.255.255.0
 gateway-list 10.5.0.1
 dns-list 10.5.0.53
 forbidden-ip 10.5.0.1 10.5.0.9
 expired day 1
#
traffic classifier voice operator or
 if-match dscp ef
#
traffic behavior voice-b
 car cir 5000 cbs 100000
#
qos policy wan
 classifier voice behavior voice-b
#
interface Ten-GigabitEthernet1/0/1
 ip address 203.0.113.2 255.255.255.252
 nat outbound 3000 address-group 1
 nat server protocol tcp global 203.0.113.10 80 inside 10.5.0.10 80
 qos apply policy wan outbound
#
interface Vlan-interface5
 ip address 10.5.0.1 255.255.255.0
#
"""

IX = """
IX Series IX2215 (magellan-sec) Software, Version 10.6.21, RELEASE SOFTWARE
System serial number: 2215X0012345
!
device-name ix-osaka
!
ip dhcp enable
ip dhcp profile lan-pool
  assignable-range 192.168.10.100 192.168.10.200
  default-gateway 192.168.10.1
  dns-server 192.168.10.53
  lease-time 24:00:00
!
interface GigaEthernet0.0
  description WAN
  ip address 203.0.113.2/30
  ip napt enable
  ip napt static 203.0.113.2 tcp 80 192.168.10.10 80
  no shutdown
!
interface GigaEthernet1.0
  description LAN
  ip address 192.168.10.1/24
  ip dhcp binding lan-pool
  no shutdown
!
"""


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text))]


# ---------------------------------------------------------------------------
# QX（Comware）
# ---------------------------------------------------------------------------


def test_qx_nat_is_read_in_both_directions():
    nat = configparse.parse(QX).nat
    assert nat is not None
    assert ("dynamic", "inside", "3000", "1", True) in {
        (r.kind, r.direction, r.matched, r.translated, r.overload) for r in nat.rules
    }
    # 外から中の1台へ通す（nat server）
    server = next(r for r in nat.rules if r.direction == "outside")
    assert (server.matched, server.translated, server.protocol) == (
        "203.0.113.10",
        "10.5.0.10",
        "tcp",
    )
    assert [p.name for p in nat.pools] == ["1"]
    assert nat.pools[0].start == "203.0.113.20"


def test_qx_acl_used_by_nat_is_not_called_unused():
    """変換で使っている ACL を「どこにも当たっていない」と言わない。"""
    assert "ACL 3000 は、どこにも当たっていません" not in titles(QX)


def test_qx_dhcp_is_read_with_what_it_does_not_hand_out():
    pool = configparse.parse(QX).dhcp.pools[0]
    assert pool.network == "10.5.0.0 255.255.255.0"
    assert pool.default_routers == ["10.5.0.1"]
    assert pool.dns_servers == ["10.5.0.53"]
    # forbidden-ip は Cisco の excluded-address と同じ扱い
    assert configparse.parse(QX).dhcp.excluded == [("10.5.0.1", "10.5.0.9")]


def test_qx_qos_keeps_what_the_behavior_does():
    qos = configparse.parse(QX).qos
    policy = qos.policies[0]
    assert policy.name == "wan"
    action = policy.classes[0]
    assert action.class_name == "voice"
    assert "car cir 5000 cbs 100000" in action.actions
    assert ("Ten-GigabitEthernet1/0/1", "output", "wan") in qos.applied


def test_qx_policy_that_is_not_applied_is_reported():
    text = QX.replace(" qos apply policy wan outbound\n", "")
    assert "優先制御の wan は、どのポートにも当たっていません" in titles(text)


def test_a_clean_qx_config_gets_no_findings():
    assert titles(QX) == []


# ---------------------------------------------------------------------------
# IX
# ---------------------------------------------------------------------------


def test_ix_napt_is_read_as_binding_to_the_outgoing_port():
    nat = configparse.parse(IX).nat
    assert nat is not None
    out = next(r for r in nat.rules if r.kind == "dynamic")
    assert (out.translated, out.overload) == ("GigaEthernet0.0", True)
    assert nat.outside_interfaces == ["GigaEthernet0.0"]
    # 内側は「napt を掛けていないポート」。片側しか無い扱いにはしない
    assert "GigaEthernet1.0" in nat.inside_interfaces


def test_ix_static_napt_keeps_the_line_as_written():
    """引数の並びは版で違う。読み取るのはアドレスまでで、原文を残す。"""
    nat = configparse.parse(IX).nat
    static = next(r for r in nat.rules if r.kind == "static-port")
    assert (static.matched, static.translated) == ("203.0.113.2", "192.168.10.10")
    assert "ip napt static" in static.raw


def test_ix_dhcp_profile_is_tied_to_the_port_it_serves():
    pool = configparse.parse(IX).dhcp.pools[0]
    assert "lan-pool" in pool.name
    assert "GigaEthernet1.0" in pool.name
    assert (pool.range_start, pool.range_end) == ("192.168.10.100", "192.168.10.200")
    assert pool.default_routers == ["192.168.10.1"]
    assert pool.dns_servers == ["192.168.10.53"]


def test_a_clean_ix_config_gets_no_findings():
    assert titles(IX) == []


def test_ix_without_a_gateway_is_reported():
    text = IX.replace("  default-gateway 192.168.10.1\n", "")
    assert [t for t in titles(text) if "既定ゲートウェイを配っていません" in t]
