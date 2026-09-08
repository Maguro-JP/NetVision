"""映像が映らない・しばらくすると止まる、の形を Config から言えること。

実機を触らずに言えるものだけを見る。どこに RP を置くべきかという設計は
要件が要るので言わない。
"""

from app.services import configparse, multicastreview
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
