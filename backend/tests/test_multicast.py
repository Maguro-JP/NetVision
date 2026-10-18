"""映像が映らない・しばらくすると止まる、の形を Config から言えること。

実機を触らずに言えるものだけを見る。どこに RP を置くべきかという設計は
要件が要るので言わない。
"""

from app.services import configparse, multicastreview
from app.services.configparse import multicast
from app.services.reviewnote import HIGH, LOW, MEDIUM


def _review(text: str):
    return multicastreview.review(configparse.parse(text))


def _one(findings, word: str):
    hits = [f for f in findings if word in f.title]
    assert len(hits) == 1, [f.title for f in findings]
    return hits[0]


CISCO = """hostname core-01
ip multicast-routing
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
 ip pim sparse-mode
!
interface GigabitEthernet0/1
 ip address 10.0.0.1 255.255.255.252
 ip pim sparse-mode
!
"""


def test_a_complete_sparse_mode_setup_says_nothing():
    text = CISCO + "ip pim rp-address 10.0.0.2\n"
    assert _review(text) == []


def test_sparse_mode_without_a_rendezvous_point_is_called_out():
    item = _one(_review(CISCO), "集合場所（RP）が決まっていません")
    assert item.level == HIGH


def test_a_candidate_based_rendezvous_point_is_accepted():
    text = CISCO + "ip pim bsr-candidate Loopback0 30\n"
    assert not any("決まっていません" in f.title for f in _review(text))


def test_a_rendezvous_point_we_cannot_reach_is_called_out():
    text = CISCO + "ip pim rp-address 172.31.9.9\n"
    item = _one(_review(text), "行き方がこの Config にありません")
    assert item.level == MEDIUM
    assert "172.31.9.9" in item.title


def test_a_rendezvous_point_behind_a_static_route_is_fine():
    text = CISCO + "ip pim rp-address 172.31.9.9\nip route 172.31.0.0 255.255.0.0 10.0.0.2\n"
    assert not any("行き方が" in f.title for f in _review(text))


def test_pim_on_only_one_port_is_noted():
    text = """hostname edge-01
ip multicast-routing
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
 ip pim sparse-mode
!
ip pim rp-address 10.10.10.9
"""
    assert _one(_review(text), "1つだけです").level == LOW


def test_multicast_turned_on_but_applied_nowhere_is_noted():
    text = "hostname sw-01\nip multicast-routing\nip igmp snooping\n"
    assert _one(_review(text), "どの口にも入れていません").level == LOW


# ---------------------------------------------------------------------------
# L2 だけの機器。聞いて回る役が居ないと、しばらくして止まる
# ---------------------------------------------------------------------------

L2 = """hostname access-01
ip igmp snooping
ip igmp snooping vlan 10
ip igmp snooping vlan 20
"""


def test_snooping_without_a_querier_is_called_out():
    item = _one(_review(L2), "querier）が居ません")
    assert item.level == MEDIUM
    assert "VLAN 10" in item.evidence


def test_a_querier_on_one_vlan_leaves_the_others_listed():
    item = _one(_review(L2 + "ip igmp snooping vlan 10 querier\n"), "querier）が居ません")
    assert item.evidence == ["VLAN 20"]


def test_a_global_querier_settles_every_vlan():
    text = L2 + "ip igmp snooping querier\n"
    assert not any("querier" in f.title for f in _review(text))


def test_a_router_in_the_same_config_is_not_asked_for_a_querier():
    text = CISCO + "ip pim rp-address 10.0.0.2\nip igmp snooping\nip igmp snooping vlan 10\n"
    assert not any("querier" in f.title for f in _review(text))


def test_a_vlan_with_snooping_turned_off_is_not_asked_for_a_querier():
    text = "hostname access-02\nip igmp snooping\nno ip igmp snooping vlan 30\n"
    assert not any("querier" in f.title for f in _review(text))


# ---------------------------------------------------------------------------
# 他のベンダーの書き方
# ---------------------------------------------------------------------------


def test_junos_is_read():
    text = """set system host-name core-02
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set protocols pim interface ge-0/0/0.0 mode sparse
set protocols pim interface ge-0/0/1.0 mode sparse
set protocols pim rp static address 10.0.0.2
"""
    parsed = configparse.parse(text)
    assert [item.name for item in parsed.multicast.pim_interfaces] == [
        "ge-0/0/0.0",
        "ge-0/0/1.0",
    ]
    assert parsed.multicast.rps[0].address == "10.0.0.2"
    assert not any("決まっていません" in f.title for f in multicastreview.review(parsed))


def test_necqx_is_read():
    text = """sysname qx-01
#
multicast routing
#
interface Vlan-interface10
 ip address 10.10.10.1 255.255.255.0
 pim sm
#
interface Vlan-interface20
 ip address 10.10.20.1 255.255.255.0
 pim sm
#
pim
 static-rp 10.10.10.9
#
"""
    parsed = configparse.parse(text)
    assert parsed.multicast.routing is True
    assert len(parsed.multicast.pim_interfaces) == 2
    assert parsed.multicast.rps[0].address == "10.10.10.9"
    assert multicastreview.review(parsed) == []


def test_nothing_written_means_nothing_read():
    parsed = configparse.parse(
        "hostname sw-99\ninterface Vlan10\n ip address 10.1.1.1 255.255.255.0\n"
    )
    assert parsed.multicast is None
    assert multicastreview.review(parsed) == []


def test_arista_writes_it_differently():
    """`router pim sparse` の中に RP、口には `pim ipv4 sparse-mode`。"""
    text = """! device: dc-01
router pim sparse
   ip rp address 10.0.0.9
interface Ethernet1
   ip address 10.0.0.1/30
   pim ipv4 sparse-mode
interface Vlan10
   ip address 10.0.10.1/24
   pim ipv4 sparse-mode
"""
    multicast = configparse.parse(text).multicast
    assert [item.name for item in multicast.pim_interfaces] == ["Ethernet1", "Vlan10"]
    assert multicast.rps[0].address == "10.0.0.9"
    assert not any("決まっていません" in f.title for f in _review(text))


def test_aoscx_writes_it_differently():
    """口の名前が離れて書かれ、`ip pim-sparse enable` で入れる。"""
    text = """hostname aruba-01
router pim
    enable
    rp-address 10.0.0.9
interface vlan 10
    ip address 10.0.10.1/24
    ip pim-sparse enable
interface vlan 20
    ip address 10.0.20.1/24
    ip pim-sparse enable
vlan 10
    ip igmp snooping enable
"""
    multicast = configparse.parse(text).multicast
    assert [item.name for item in multicast.pim_interfaces] == ["vlan 10", "vlan 20"]
    assert multicast.rps[0].address == "10.0.0.9"
    assert [item.vlan for item in multicast.vlans] == [10]


def test_the_group_range_on_a_rendezvous_point_is_kept_without_the_keyword():
    text = """hostname nx-01
feature pim
ip pim rp-address 10.0.0.9 group-list 239.0.0.0/8
interface Ethernet1/1
  ip pim sparse-mode
"""
    rp = configparse.parse(text).multicast.rps[0]
    assert rp.address == "10.0.0.9"
    assert rp.scope == "239.0.0.0/8"


def test_override_is_not_mistaken_for_a_group_range():
    text = CISCO + "ip pim rp-address 10.0.0.2 override\n"
    assert configparse.parse(text).multicast.rps[0].scope is None


def test_a_plain_vlan_definition_is_not_read_as_snooping():
    """`vlan 10` と書いてあるだけの機器に、マルチキャストの話を持ち出さない。

    show の出力に IGMP という語が混ざるだけで拾ってしまうと、設定していない
    機器にまで指摘が出る。デモでも現場でも、これは信用をなくす。
    """
    text = """hostname acc-sw-01
vlan 10
 name USERS
!
vlan 30
 name MGMT
!
show cdp neighbors detail
-------------------------
Device ID: core-sw-01
Platform: cisco C9500-40X,  Capabilities: Switch IGMP
Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet1/0/1
"""
    parsed = configparse.parse(text)
    assert parsed.multicast is None
    assert multicastreview.review(parsed) == []


def test_snooping_written_inside_a_vlan_block_is_still_read():
    text = """hostname sw-01
vlan 10
 name USERS
 ip igmp snooping enable
!
"""
    parsed = configparse.parse(text)
    assert [item.vlan for item in parsed.multicast.vlans] == [10]


def test_bidir_is_not_mistaken_for_a_group_range():
    """Cisco: `ip pim rp-address A [access-list] [override] [bidir]`。

    出典: Command Reference, Cisco IOS XE Gibraltar 16.12.x (Catalyst 9600)
    「IP Multicast Routing Commands」の ip pim rp-address。
    """
    text = CISCO + "ip pim rp-address 10.0.0.2 5 override bidir\n"
    assert configparse.parse(text).multicast.rps[0].scope == "5"


HUAWEI_SNOOP = """sysname sw-01
#
igmp-snooping enable
#
vlan 3
 igmp-snooping enable
 igmp-snooping querier enable
#
vlan 4
 igmp-snooping enable
#
"""


def test_huawei_querier_is_read():
    """Huawei は VLAN ビューに `igmp-snooping querier enable` と書く。

    出典: Huawei S300/S500/S2700/S5700/S6700 V200R024C00 Command Reference の
    igmp-snooping querier enable（例は `vlan 3` の中）。
    """
    found = multicast.parse(HUAWEI_SNOOP, "undoform")
    assert found.snooping is True
    assert {item.vlan: item.querier for item in found.vlans} == {3: True, 4: False}


def test_h3c_turns_on_snooping_for_many_vlans_at_once():
    """H3C は `igmp-snooping` で全体に入れ、囲みの中の `enable vlan 2 to 10` で帯に入れる。

    出典: H3C「IGMP snooping commands」（Comware 7）の igmp-snooping と
    enable (IGMP-snooping view)、VLAN ビューの igmp-snooping querier。
    """
    text = """sysname h3c-01
#
igmp-snooping
 enable vlan 2 5 to 7
#
vlan 5
 igmp-snooping querier
#
"""
    found = multicast.parse(text, "undoform")
    assert found.snooping is True
    assert sorted(item.vlan for item in found.vlans) == [2, 5, 6, 7]
    assert [item.vlan for item in found.vlans if item.querier] == [5]


def test_enable_vlan_outside_the_snooping_view_is_not_read():
    """`enable vlan` は IGMP-snooping の囲みの中だけ。他の囲みの同じ語は拾わない。"""
    text = """sysname h3c-01
#
igmp-snooping
#
stp region-configuration
 enable vlan 10
#
"""
    found = multicast.parse(text, "undoform")
    assert found is None or found.vlans == []


def test_huawei_static_rp_keeps_only_the_acl():
    """`static-rp A [acl 番号 | acl-name 名前] [preferred]`。preferred は範囲ではない。

    出典: Huawei V200R024C00 / V200R025C00 Command Reference の static-rp (IPv4)。
    """
    text = """multicast routing-enable
#
interface Vlanif10
 pim sm
#
pim
 static-rp 10.110.0.6 2001 preferred
 static-rp 10.110.0.7 acl-name myacl preferred
 static-rp 10.110.0.8 preferred
#
"""
    found = multicast.parse(text, "undoform")
    assert [(rp.address, rp.scope) for rp in found.rps] == [
        ("10.110.0.6", "2001"),
        ("10.110.0.7", "myacl"),
        ("10.110.0.8", None),
    ]


NXOS_SNOOP = """hostname nx-01
ip igmp snooping
vlan 1-3
vlan configuration 2
  ip igmp snooping
  ip igmp snooping fast-leave
  ip igmp snooping querier 172.20.52.106
  ip igmp snooping querier-timeout 300
vlan configuration 3
  no ip igmp snooping
"""


def test_nxos_vlan_configuration_is_read():
    """NX-OS は帯ごとの設定を `vlan configuration 2` の囲みの中に書く。

    出典: Cisco Nexus 9000 Series NX-OS Multicast Routing Configuration Guide
    （7.x / 10.2(x) / 10.3(x)）「Configuring IGMP Snooping」の Configuration Examples。
    """
    found = multicast.parse(NXOS_SNOOP, "cisco")
    assert found.snooping is True
    states = {item.vlan: (item.enabled, item.querier) for item in found.vlans}
    assert states == {2: (True, True), 3: (False, False)}


def test_querier_timeout_is_not_a_querier():
    """`querier-timeout` は待ち時間の設定で、querier を置く行ではない。"""
    text = """hostname nx-01
vlan configuration 5
  ip igmp snooping
  ip igmp snooping querier-timeout 300
"""
    found = multicast.parse(text, "cisco")
    assert [(item.vlan, item.querier) for item in found.vlans] == [(5, False)]


def test_a_vlan_configuration_range_is_opened():
    text = """hostname nx-01
vlan configuration 10,20-21
  ip igmp snooping querier 10.0.0.9
"""
    found = multicast.parse(text, "cisco")
    assert sorted((item.vlan, item.querier) for item in found.vlans) == [
        (10, True),
        (20, True),
        (21, True),
    ]


JUNOS_L3 = """set system host-name core-01
set vlans SALES vlan-id 110
set vlans ENG vlan-id 120
set protocols pim rp local family inet address 172.18.19.254
set protocols pim interface all mode sparse
set protocols pim interface fxp0.0 disable
set protocols igmp-snooping vlan SALES l2-querier source-address 10.1.10.2
set protocols igmp-snooping vlan ENG
"""


def test_junos_follows_the_official_form():
    """Junos の RP を自分で持つ書き方、管理口の除外、VLAN 名での snooping。

    出典: Juniper「Enterprise Layer 3 Multicast」Implementation Guide の設定例と、
    Junos OS「IGMP Snooping Overview」の `l2-querier source-address`。
    """
    found = multicast.parse(JUNOS_L3, "juniper")
    assert [rp.address for rp in found.rps] == ["172.18.19.254"]
    assert found.dynamic_rp is False
    assert [item.name for item in found.pim_interfaces] == ["all"]
    states = {item.vlan: item.querier for item in found.vlans}
    assert states == {110: True, 120: False}


def test_junos_snooping_turned_off_on_a_legacy_ex_switch():
    """旧 EX（ELS でない）は既定で全帯が有効で、`disable` で帯ごとに止める。

    出典: Junos OS「vlan (IGMP Snooping)」の disable。
    """
    text = """set vlans CAMERA vlan-id 30
set protocols igmp-snooping vlan CAMERA disable
"""
    found = multicast.parse(text, "juniper")
    assert [(item.vlan, item.enabled) for item in found.vlans] == [(30, False)]


def test_aoscx_candidate_rp_inside_router_pim_is_read():
    """AOS-CX は候補方式を `router pim` の中に書く。RP が決まっていない扱いにしない。

    出典: AOS-CX Multicast Guide 10.09「PIM-SM configuration example」の Router1。
    """
    text = """router pim
    enable
    bsr-candidate source-ip-interface 1/1/1
    rp-candidate source-ip-interface 1/1/1
    rp-candidate group-prefix 224.0.0.0/4
interface 1/1/1
    ip address 10.10.10.1/24
    ip pim-sparse enable
interface 1/1/2
    ip address 20.20.20.1/24
    ip pim-sparse enable
"""
    found = multicast.parse(text, "aruba")
    assert found.dynamic_rp is True
    assert found.rps == []


def test_aoscx_igmp_on_the_vlan_interface_is_the_querier():
    """AOS-CX は帯の口で `ip igmp enable` にすると、その帯の querier になれる。

    出典: AOS-CX 10.10 Multicast Guide（4100i/6000/6100）の IGMP の章
    （Enabling IGMP allows the router to become querier）と「IGMP configuration example」。
    """
    text = """vlan 2
    ip igmp snooping enable
vlan 3
    ip igmp snooping enable
vlan 4
    ip igmp snooping disable
interface vlan2
    ip address 20.1.1.1/24
    ip igmp enable
"""
    found = multicast.parse(text, "aruba")
    states = {item.vlan: (item.enabled, item.querier) for item in found.vlans}
    assert states == {2: (True, True), 3: (True, False), 4: (False, False)}


def test_arista_rp_address_inside_the_ipv4_submode_is_read():
    """Arista の RP は `router pim sparse-mode` の `ipv4` の中に `rp address` と書く。

    出典: Arista EOS 4.36.2F User Manual「Protocol Independent Multicast」の rp address
    （`rp address 10.17.255.83 override`、`rp address 10.21.18.23 238.1.12.0/24`）。
    """
    text = """router multicast
   ipv4
      routing
router pim sparse-mode
   ipv4
      rp address 10.17.255.83 override
      rp address 10.21.18.23 238.1.12.0/24
interface Vlan10
   ip address 10.0.10.1/24
   pim ipv4 sparse-mode
"""
    found = multicast.parse(text, "arista")
    assert [(rp.address, rp.scope) for rp in found.rps] == [
        ("10.17.255.83", None),
        ("10.21.18.23", "238.1.12.0/24"),
    ]
    assert not any("決まっていません" in f.title for f in _review(text))


def test_arista_candidate_rp_is_read():
    """出典: Arista EOS 4.36.2F User Manual の rp candidate。"""
    text = """router pim sparse-mode
   ipv4
      rp candidate vlan 24 235.1.1.0/24 priority 48 interval 45
interface Vlan24
   pim ipv4 sparse-mode
"""
    found = multicast.parse(text, "arista")
    assert found.dynamic_rp is True


def test_arista_snooping_querier_follows_the_vlan_setting_over_the_global_one():
    """Arista の帯ごとの querier は、範囲で書け、全体の設定より優先する。

    出典: Arista EOS 4.35.1F User Manual「IGMP and IGMP Snooping」の Snooping Querier
    （VLAN querier settings take precedence over the global querier setting。例
    `no ip igmp snooping vlan 1-4 querier`、`ip igmp snooping vlan 5-8 querier`）。
    """
    text = (
        "ip igmp snooping\n"
        "ip igmp snooping querier\n"
        "ip igmp snooping querier address 10.0.0.1\n"
        "no ip igmp snooping vlan 1-4 querier\n"
        "ip igmp snooping vlan 5-8 querier\n"
    )
    found = multicast.parse(text, "arista")
    assert found is not None
    querier = {item.vlan: item.querier for item in found.vlans}
    assert querier == {1: False, 2: False, 3: False, 4: False, 5: True, 6: True, 7: True, 8: True}
