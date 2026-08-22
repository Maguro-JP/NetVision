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
    finding = next(f for f in report.findings if "届きません" in f.title)
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
