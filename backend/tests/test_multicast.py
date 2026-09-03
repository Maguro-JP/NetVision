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


def test_comware_is_read():
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
