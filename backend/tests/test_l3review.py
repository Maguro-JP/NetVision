"""L3 の中身を並べ、気になるところを指摘する。

経路は間違っていても半分は動くので、気づくのが遅れる。Config から読める
範囲で先に挙げること、そして必ず理由と根拠を添えることを確かめる。
"""

import pytest

from app.models import Device
from app.models.topology import Link, LinkOrigin
from app.services import l3review
from tests.conftest import API


def _snapshot(db, device, content, digest):
    from app.models.topology import ConfigSnapshot

    db.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=device.hostname,
            filename=f"{device.hostname}.txt",
            content=content,
            sha256=digest,
        )
    )
    db.commit()


def _device(db, hostname, **kwargs):
    device = Device(hostname=hostname, vendor="Cisco", model="C9500-40X", **kwargs)
    db.add(device)
    db.flush()
    return device


HEALTHY = """hostname core-sw-01
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
!
router ospf 1
 network 10.10.10.0 0.0.0.255 area 0
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
"""


@pytest.fixture()
def core(db_session):
    device = _device(db_session, "core-sw-01")
    _snapshot(db_session, device, HEALTHY, "a" * 64)
    return device


def test_without_a_config_it_reports_nothing_rather_than_guessing(db_session):
    device = _device(db_session, "no-config-sw")
    db_session.commit()
    report = l3review.review(db_session, device)
    assert report.has_config is False
    assert report.findings == []


def test_a_tidy_config_gets_no_complaints(db_session, core):
    report = l3review.review(db_session, core)
    assert report.has_config is True
    assert report.routes[0]["destination"] == "0.0.0.0/0"
    assert report.ospf["areas"][0]["area"] == "0"
    assert report.findings == []


def test_a_next_hop_nobody_can_reach_is_the_first_thing_flagged(db_session):
    """隣に居ない相手はネクストホップに指せない。付け替えの直し忘れで残る。"""
    device = _device(db_session, "edge-sw-01")
    _snapshot(
        db_session,
        device,
        "hostname edge-sw-01\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 0.0.0.0 0.0.0.0 192.168.99.254\n",
        "b" * 64,
    )
    report = l3review.review(db_session, device)
    finding = next(f for f in report.findings if "行けません" in f.title)
    # 逃げ道になる既定経路が、その落ちている既定経路そのものしか無い
    assert finding.level == l3review.HIGH
    assert finding.why
    assert any("192.168.99.254" in line for line in finding.evidence)


def test_two_routes_with_the_same_weight_are_called_out(db_session):
    device = _device(db_session, "rt-01")
    _snapshot(
        db_session,
        device,
        "hostname rt-01\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 0.0.0.0 0.0.0.0 10.10.10.254\n"
        "ip route 0.0.0.0 0.0.0.0 10.10.10.253\n",
        "c" * 64,
    )
    report = l3review.review(db_session, device)
    assert any("同じ重み" in f.title for f in report.findings)


def test_a_standby_route_with_a_higher_distance_is_left_alone(db_session):
    """距離が違うなら、待機側を用意した形。意図が読めるので言わない。"""
    device = _device(db_session, "rt-02")
    _snapshot(
        db_session,
        device,
        "hostname rt-02\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 0.0.0.0 0.0.0.0 10.10.10.254\n"
        "ip route 0.0.0.0 0.0.0.0 10.10.10.253 200\n",
        "d" * 64,
    )
    report = l3review.review(db_session, device)
    assert not any("同じ重み" in f.title for f in report.findings)


def test_a_segment_nobody_advertises_is_flagged(db_session):
    device = _device(db_session, "rt-03")
    _snapshot(
        db_session,
        device,
        "hostname rt-03\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "interface Vlan20\n ip address 10.10.20.1 255.255.255.0\n!\n"
        "router ospf 1\n network 10.10.10.0 0.0.0.255 area 0\n",
        "e" * 64,
    )
    report = l3review.review(db_session, device)
    finding = next(f for f in report.findings if "広告されていない" in f.title)
    assert any("10.10.20.0/24" in line for line in finding.evidence)


def test_a_segment_carried_by_eigrp_or_isis_counts_as_advertised(db_session):
    """OSPF と BGP だけ見ていると、EIGRP や IS-IS で広告している口を入れ忘れと言ってしまう。"""
    device = _device(db_session, "rt-03b")
    _snapshot(
        db_session,
        device,
        "hostname rt-03b\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "interface Vlan20\n ip address 10.10.20.1 255.255.255.0\n!\n"
        "router eigrp 100\n network 10.10.10.0 0.0.0.255\n network 10.10.20.0 0.0.0.255\n",
        "e" * 63 + "1",
    )
    report = l3review.review(db_session, device)
    assert not any("広告されていない" in f.title for f in report.findings)

    xr = _device(db_session, "pe-xr")
    _snapshot(
        db_session,
        xr,
        "RP/0/RP0/CPU0:pe1#show running-config\nhostname pe-xr\n"
        "interface HundredGigE0/0/0/0\n ipv4 address 10.1.1.0 255.255.255.254\n"
        "router isis CORE\n net 49.0001.0000.0000.0001.00\n interface HundredGigE0/0/0/0\n"
        "  address-family ipv4 unicast\n",
        "e" * 63 + "2",
    )
    report = l3review.review(db_session, xr)
    assert not any("広告されていない" in f.title for f in report.findings)


def test_a_static_only_config_is_not_nagged_about_advertising(db_session):
    """静的で回している構成では、載っていないのが普通。"""
    device = _device(db_session, "rt-04")
    _snapshot(
        db_session,
        device,
        "hostname rt-04\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 0.0.0.0 0.0.0.0 10.10.10.254\n",
        "f" * 64,
    )
    report = l3review.review(db_session, device)
    assert not any("広告されていない" in f.title for f in report.findings)


def test_a_missing_default_route_is_mentioned_only_when_static_only(db_session):
    device = _device(db_session, "rt-05")
    _snapshot(
        db_session,
        device,
        "hostname rt-05\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 192.168.10.0 255.255.255.0 10.10.10.254\n",
        "1" * 64,
    )
    report = l3review.review(db_session, device)
    assert any("既定の経路" in f.title for f in report.findings)


REDUNDANT = """hostname dist-sw-01
!
interface Vlan10
 ip address 10.10.10.2 255.255.255.0
 standby 10 ip 10.10.10.1
 standby 10 priority 110
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
"""


def test_redundancy_on_only_one_side_is_the_kind_that_bites_on_the_day(db_session):
    left = _device(db_session, "dist-sw-01")
    right = _device(db_session, "dist-sw-02")
    db_session.add(
        Link(
            a_device_id=left.id,
            a_interface="Gi1/0/1",
            b_device_id=right.id,
            b_interface="Gi1/0/1",
            origin=LinkOrigin.MANUAL,
        )
    )
    _snapshot(db_session, left, REDUNDANT, "2" * 64)
    _snapshot(
        db_session,
        right,
        "hostname dist-sw-02\n!\ninterface Vlan10\n ip address 10.10.10.3 255.255.255.0\n",
        "3" * 64,
    )
    report = l3review.review(db_session, left)
    finding = next(f for f in report.findings if "片側だけ" in f.title)
    assert finding.level == l3review.HIGH
    assert any("dist-sw-02" in line for line in finding.evidence)


def test_the_same_priority_on_both_sides_is_flagged_too(db_session):
    left = _device(db_session, "dist-sw-11")
    right = _device(db_session, "dist-sw-12")
    db_session.add(
        Link(
            a_device_id=left.id,
            a_interface="Gi1/0/1",
            b_device_id=right.id,
            b_interface="Gi1/0/1",
            origin=LinkOrigin.MANUAL,
        )
    )
    _snapshot(db_session, left, REDUNDANT.replace("dist-sw-01", "dist-sw-11"), "4" * 64)
    _snapshot(
        db_session,
        right,
        REDUNDANT.replace("dist-sw-01", "dist-sw-12").replace("10.10.10.2 ", "10.10.10.3 "),
        "5" * 64,
    )
    report = l3review.review(db_session, left)
    assert any("優先度が同じ" in f.title for f in report.findings)


def test_a_higher_priority_without_preempt_never_takes_the_role_back(db_session):
    """HSRP は既定で役を取り返さない。優先度だけ上げても主に戻らない。

    出典: Cisco IOS First Hop Redundancy Protocols Command Reference の
    standby preempt、First Hop Redundancy Protocols Configuration Guide,
    Cisco IOS Release 15S「HSRP Priority and Preemption」
    （If you do not use the standby preempt interface configuration command …
    that router will not become the active router, even if its priority is
    higher than all other routers）。
    """
    device = _device(db_session, "dist-sw-21")
    _snapshot(db_session, device, REDUNDANT.replace("dist-sw-01", "dist-sw-21"), "6" * 64)
    report = l3review.review(db_session, device)
    finding = next(f for f in report.findings if "戻りません" in f.title)
    assert any("優先度 110" in line for line in finding.evidence)


def test_preempt_written_or_a_default_priority_says_nothing(db_session):
    """preempt があるとき、優先度を上げていないときは言わない。"""
    with_preempt = _device(db_session, "dist-sw-22")
    _snapshot(
        db_session,
        with_preempt,
        REDUNDANT.replace("dist-sw-01", "dist-sw-22").replace(
            " standby 10 priority 110\n", " standby 10 priority 110\n standby 10 preempt\n"
        ),
        "7" * 64,
    )
    assert not [
        f for f in l3review.review(db_session, with_preempt).findings if "戻りません" in f.title
    ]

    plain = _device(db_session, "dist-sw-23")
    _snapshot(
        db_session,
        plain,
        REDUNDANT.replace("dist-sw-01", "dist-sw-23").replace(" standby 10 priority 110\n", ""),
        "8" * 64,
    )
    assert not [f for f in l3review.review(db_session, plain).findings if "戻りません" in f.title]


def test_all_passive_ospf_never_gets_a_neighbour(db_session):
    """既定を passive にしたまま、どの口も戻していない。

    出典: IP Routing Configuration Guide, Cisco IOS XE「Default Passive
    Interfaces」（In OSPF, hello packets are not sent on an interface that is
    specified as passive. Hence, the device is not able to discover any
    neighbors。no passive-interface … Activates only those interfaces that
    need adjacencies）。
    """
    device = _device(db_session, "rt-31")
    _snapshot(
        db_session,
        device,
        "hostname rt-31\n!\ninterface Vlan10\n ip address 10.31.0.1 255.255.255.0\n!\n"
        "router ospf 1\n router-id 10.31.0.1\n network 10.31.0.0 0.0.255.255 area 0\n"
        " passive-interface default\n",
        "9" * 64,
    )
    report = l3review.review(db_session, device)
    assert any("全部 passive" in f.title for f in report.findings)


def test_a_port_taken_out_of_passive_is_not_counted_as_passive(db_session):
    """`no passive-interface X` を passive の側に数えない（逆の意味）。"""
    device = _device(db_session, "rt-32")
    _snapshot(
        db_session,
        device,
        "hostname rt-32\n!\ninterface Vlan10\n ip address 10.32.0.1 255.255.255.0\n!\n"
        "router ospf 1\n router-id 10.32.0.1\n network 10.32.0.0 0.0.255.255 area 0\n"
        " passive-interface default\n no passive-interface Vlan10\n",
        "a" * 64,
    )
    report = l3review.review(db_session, device)
    assert not [f for f in report.findings if "全部 passive" in f.title]
    assert report.ospf["passive_interfaces"] == []
    assert report.ospf["active_interfaces"] == ["Vlan10"]


def test_every_finding_says_why_and_shows_what_it_looked_at(db_session):
    device = _device(db_session, "rt-06")
    _snapshot(
        db_session,
        device,
        "hostname rt-06\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "ip route 0.0.0.0 0.0.0.0 192.168.99.254\n",
        "6" * 64,
    )
    report = l3review.review(db_session, device)
    assert report.findings
    for finding in report.findings:
        assert finding.why
        assert finding.evidence


def test_the_api_returns_the_l3_and_the_findings(client, db_session, core):
    res = client.get(f"{API}/devices/{core.id}/l3")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_config"] is True
    assert body["routes"][0]["is_default"] is True
    assert body["ospf"]["process_id"] == "1"
    assert body["findings"] == []


def test_the_api_404s_for_an_unknown_device(client):
    assert client.get(f"{API}/devices/9999/l3").status_code == 404


def test_a_tool_nobody_uses_is_mentioned_but_not_alarmed_about(db_session):
    """消し忘れなら害はないが、当て先の書き忘れなら絞れていないまま残る。"""
    device = _device(db_session, "rt-07")
    _snapshot(
        db_session,
        device,
        "hostname rt-07\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "route-map UNUSED permit 10\n!\nip route 0.0.0.0 0.0.0.0 10.10.10.254\n",
        "7" * 64,
    )
    report = l3review.review(db_session, device)
    finding = next(f for f in report.findings if "使われていない" in f.title)
    assert finding.level == l3review.LOW
    assert any("UNUSED" in line for line in finding.evidence)


def test_the_other_protocols_show_up_in_the_report(db_session):
    device = _device(db_session, "rt-08")
    _snapshot(
        db_session,
        device,
        "hostname rt-08\n!\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n!\n"
        "router eigrp 100\n network 10.10.0.0 0.0.255.255\n",
        "8" * 64,
    )
    report = l3review.review(db_session, device)
    assert report.protocols[0]["name"] == "eigrp"
    assert report.protocols[0]["networks"] == ["10.10.0.0/16"]


CONTROLS = """hostname rt-09
!
interface GigabitEthernet0/0
 ip address 203.0.113.2 255.255.255.252
 ip nat outside
 ip access-group OUTSIDE-IN in
 crypto map VPNMAP
!
interface GigabitEthernet0/1
 ip address 10.10.0.1 255.255.255.0
 ip nat inside
 service-policy output SHAPE
!
ip access-list extended OUTSIDE-IN
 10 permit tcp any host 203.0.113.10 eq 443
 20 deny ip any any
!
ip nat inside source list OUTSIDE-IN interface GigabitEthernet0/0 overload
!
ip dhcp excluded-address 10.10.0.1 10.10.0.10
ip dhcp pool OFFICE
 network 10.10.0.0 255.255.255.0
 default-router 10.10.0.1
 dns-server 10.10.0.53
!
class-map match-any VOICE
 match dscp ef
policy-map SHAPE
 class VOICE
  priority percent 20
!
crypto ipsec transform-set TS esp-aes 256 esp-sha256-hmac
crypto map VPNMAP 10 ipsec-isakmp
 set peer 198.51.100.1
 set transform-set TS
 set pfs group14
 match address OUTSIDE-IN
!
router bgp 65001
 neighbor 203.0.113.1 remote-as 65100
 neighbor 203.0.113.1 prefix-list CUST out
 neighbor 203.0.113.1 maximum-prefix 500
!
ip prefix-list CUST seq 5 permit 10.10.0.0/16 le 24
"""


def test_the_report_says_where_each_control_is_applied(db_session):
    """書いてあるだけのものと、効いているものを、同じ表で見分けられること。"""
    device = _device(db_session, "rt-09")
    _snapshot(db_session, device, CONTROLS, "9" * 64)
    report = l3review.review(db_session, device)

    kinds = {entry["kind"] for entry in report.controls}
    assert kinds == {"通信の制限", "アドレス変換", "アドレス配布", "優先制御", "拠点間の暗号化"}
    acl = next(e for e in report.controls if e["kind"] == "通信の制限")
    assert "interface GigabitEthernet0/0 入り" in acl["applied_to"]
    vpn = next(e for e in report.controls if e["kind"] == "拠点間の暗号化")
    assert vpn["applied_to"] == ["interface GigabitEthernet0/0"]


def test_a_bgp_peer_shows_what_is_applied_to_it(db_session):
    """BGP は相手ごとに設定が違う。1台だけ抜けているのを見えるようにする。"""
    device = _device(db_session, "rt-10")
    _snapshot(db_session, device, CONTROLS, "a" * 63 + "b")
    report = l3review.review(db_session, device)

    neighbor = report.bgp["neighbors"][0]
    assert neighbor["external"] is True
    assert neighbor["prefix_list_out"] == "CUST"
    assert neighbor["maximum_prefix"] == 500


def test_the_api_carries_the_controls(client, core):
    body = client.get(f"{API}/devices/{core.id}/l3").json()
    assert "controls" in body


def test_the_report_shows_the_acl_line_by_line_with_the_unread_ones_marked(db_session):
    """なぜ通らないのかは、順番のまま見えないと追えない。"""
    device = _device(db_session, "rt-11")
    _snapshot(
        db_session,
        device,
        "hostname rt-11\n!\ninterface GigabitEthernet0/0\n ip access-group OUT in\n!\n"
        "ip access-list extended OUT\n permit ip any any\n deny tcp any any eq 23\n",
        "c" * 64,
    )
    report = l3review.review(db_session, device)

    acl = next(entry for entry in report.controls if entry["kind"] == "通信の制限")
    assert [row["reads"] for row in acl["rows"]] == [True, False]
    assert acl["rows"][1]["hidden_by"] == "permit ip any any"


def test_the_aps_of_a_controller_are_listed_with_the_ssids(db_session):
    """コントローラの一覧にある AP を、SSID と同じ表に出す。"""
    from app.services import l3review

    wlc = _device(db_session, "wlc-01")
    content = (
        "hostname wlc-01\n"
        "wlan OFFICE 1 OFFICE\n"
        " security wpa psk set-key ascii 0 himitsu\n"
        " no shutdown\n"
        "show ap summary\n"
        "AP Name   Slots AP Model    Ethernet MAC    Radio MAC       "
        "Location Country IP Address  State\n"
        "AP-3F-01  2     C9120AXI-Q  0011.2233.4455  0011.2233.4460  "
        "3F east  JP      10.0.30.11  Registered\n"
    )
    _snapshot(db_session, wlc, content, "a" * 64)

    report = l3review.review(db_session, wlc)
    aps = [c for c in report.controls if c["kind"] == "AP"]
    assert len(aps) == 1
    assert aps[0]["name"] == "AP-3F-01"
    assert "C9120AXI-Q" in aps[0]["detail"]
    assert "10.0.30.11" in aps[0]["detail"]
    assert "3F east" in aps[0]["detail"]


def _pair(db, left_name, right_name, left_config, right_config, digests):
    left = _device(db, left_name)
    right = _device(db, right_name)
    db.add(
        Link(
            a_device_id=left.id,
            a_interface="Gi1/0/1",
            b_device_id=right.id,
            b_interface="Gi1/0/1",
            origin=LinkOrigin.MANUAL,
        )
    )
    _snapshot(db, left, left_config, digests[0])
    _snapshot(db, right, right_config, digests[1])
    return left


def test_a_vrrp_address_owner_is_not_called_the_same_priority(db_session):
    """仮想アドレスが自分の口のアドレスと同じ機器（持ち主）は、優先度が 255 に固定される。

    どちらも priority を書いていなくても、「優先度が同じ」ではない。

    出典: Cisco「Network Services Configuration Guide - Configuring VRRP」（IOS XE）の
    VRRP Router Priority and Preemption（「If a VRRP router owns the IP address of the virtual
    router and the IP address of the physical interface, this router will function as a virtual
    primary router.」「The default priority is 100.」）と Configuring VRRP Object Tracking
    （「If a VRRP group is the IP address owner, its priority is fixed at 255」）。
    """
    owner = (
        "hostname dist-sw-31\n!\ninterface Vlan10\n ip address 10.10.10.2 255.255.255.0\n"
        " vrrp 10 ip 10.10.10.2\n!\n"
    )
    backup = (
        "hostname dist-sw-32\n!\ninterface Vlan10\n ip address 10.10.10.3 255.255.255.0\n"
        " vrrp 10 ip 10.10.10.2\n!\n"
    )
    left = _pair(db_session, "dist-sw-31", "dist-sw-32", owner, backup, ("6" * 64, "7" * 64))
    report = l3review.review(db_session, left)
    assert not [f.title for f in report.findings if "優先度が同じ" in f.title]


def test_an_unwritten_priority_is_the_default_100(db_session):
    """priority を書かなければ既定の 100。片側が 100 を明示していても、同じ優先度。

    出典: 上の試験と同じ（「The default priority is 100.」）と、Cisco IOS First Hop Redundancy
    Protocols Command Reference の standby priority（既定 100）。
    """
    left_config = REDUNDANT.replace("dist-sw-01", "dist-sw-41").replace(
        " standby 10 priority 110\n", " standby 10 priority 100\n"
    )
    right_config = (
        REDUNDANT.replace("dist-sw-01", "dist-sw-42")
        .replace("10.10.10.2 ", "10.10.10.3 ")
        .replace(" standby 10 priority 110\n", "")
    )
    left = _pair(
        db_session, "dist-sw-41", "dist-sw-42", left_config, right_config, ("8" * 64, "9" * 64)
    )
    report = l3review.review(db_session, left)
    finding = next(f for f in report.findings if "優先度が同じ" in f.title)
    assert any("優先度 100" in line for line in finding.evidence)
