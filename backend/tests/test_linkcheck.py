"""線の両端で、設定がそろっているかを見る。

1台ずつの Config を見ても分からない壊れ方がある。片側だけトランク、片側だけ
大きい MTU、片側だけ速度を固定。どれも片方の機器の中では正しいので、1台ずつ
見ている限り見つからない。しかも通信が止まらないので、何か月も残る。
"""

from app.models import Device
from app.models.topology import Interface, Link
from app.services import linkcheck
from app.services.configparse import normalize
from app.services.l3review import HIGH, MEDIUM
from tests.conftest import API


def _pair(db, left: dict, right: dict) -> Link:
    a = Device(hostname="sw-a", vendor="Cisco", model="C9300")
    b = Device(hostname="sw-b", vendor="Cisco", model="C9300")
    db.add_all([a, b])
    db.flush()

    for device, values in ((a, left), (b, right)):
        name = values.pop("name", "GigabitEthernet1/0/1")
        db.add(
            Interface(
                device_id=device.id,
                name=name,
                normalized_name=normalize(name),
                **values,
            )
        )
    link = Link(
        a_device_id=a.id,
        a_interface="GigabitEthernet1/0/1",
        b_device_id=b.id,
        b_interface="GigabitEthernet1/0/1",
    )
    db.add(link)
    db.commit()
    return link


def _titles(db) -> list[str]:
    return [item.finding.title for item in linkcheck.review(db).findings]


def test_a_tidy_pair_gets_no_complaints(db_session):
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "10,20", "native_vlan": 1, "mtu": 9198},
        {"mode": "trunk", "trunk_vlans": "10,20", "native_vlan": 1, "mtu": 9198},
    )
    assert _titles(db_session) == []


def test_trunk_facing_access_is_the_heaviest(db_session):
    """1つの VLAN だけがたまたま通り、残りは通らない。"""
    _pair(db_session, {"mode": "trunk"}, {"mode": "access", "vlan": 10})
    report = linkcheck.review(db_session)
    assert report.findings[0].finding.level == HIGH
    assert "トランク" in report.findings[0].finding.title


def test_allowed_vlans_that_do_not_match_are_listed_both_ways(db_session):
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "10,20,30"},
        {"mode": "trunk", "trunk_vlans": "10,40"},
    )
    finding = next(
        item.finding
        for item in linkcheck.review(db_session).findings
        if "通す VLAN" in item.finding.title
    )
    assert "20, 30" in finding.evidence[0]
    assert "40" in finding.evidence[1]


def test_all_means_it_depends_on_the_other_side_so_nothing_is_said(db_session):
    """「全部通す」の中身は、その機器に定義してある VLAN 次第で変わる。"""
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "all"},
        {"mode": "trunk", "trunk_vlans": "10,20"},
    )
    assert not any("通す VLAN" in title for title in _titles(db_session))


def test_a_different_untagged_vlan_is_called_out(db_session):
    """分けたつもりのものが、そこだけ混ざる。"""
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "10,20", "native_vlan": 1},
        {"mode": "trunk", "trunk_vlans": "10,20", "native_vlan": 99},
    )
    finding = next(
        item.finding
        for item in linkcheck.review(db_session).findings
        if "タグ無し" in item.finding.title
    )
    assert finding.level == HIGH
    assert "VLAN 99" in " ".join(finding.evidence)


def test_a_different_mtu_is_explained_by_what_it_looks_like(db_session):
    """普段は動いていて、大きい荷物のときだけ固まる。"""
    _pair(db_session, {"mtu": 9198}, {"mtu": 1500})
    finding = next(
        item.finding
        for item in linkcheck.review(db_session).findings
        if "MTU" in item.finding.title
    )
    assert finding.level == MEDIUM
    assert "大きい荷物" in finding.why


def test_one_side_fixed_and_one_side_auto_is_called_out(db_session):
    _pair(db_session, {"speed": "1000", "duplex": "full"}, {})
    titles = " / ".join(_titles(db_session))
    assert "片側だけ速度を固定" in titles
    assert "片側だけ二重を固定" in titles


def test_bundling_on_one_side_only_is_heavy(db_session):
    """束にならないばかりか、線が輪になって回り続けることがある。"""
    _pair(db_session, {"lag_group": 1, "lag_mode": "active"}, {})
    finding = next(
        item.finding
        for item in linkcheck.review(db_session).findings
        if "束ねて" in item.finding.title
    )
    assert finding.level == HIGH


def test_different_bundling_modes_are_called_out(db_session):
    _pair(
        db_session,
        {"lag_group": 1, "lag_mode": "active"},
        {"lag_group": 1, "lag_mode": "on"},
    )
    assert any("束ね方" in title for title in _titles(db_session))


def test_every_finding_points_at_the_line_it_is_about(db_session):
    """どの線かが分からないと、直しに行けない。"""
    link = _pair(db_session, {"mode": "trunk"}, {"mode": "access"})
    for item in linkcheck.review(db_session).findings:
        assert item.link_id == link.id
        assert item.a_hostname == "sw-a"
        assert item.b_hostname == "sw-b"
        assert item.finding.why
        assert item.finding.evidence


def test_without_any_line_it_says_what_to_do(db_session):
    report = linkcheck.review(db_session)
    assert report.links_seen == 0
    assert any("結線表" in note for note in report.notes)


def test_a_line_whose_ports_are_unknown_is_counted_but_not_judged(db_session):
    """設定が読めていないものを、そろっていると言ってはいけない。"""
    a = Device(hostname="sw-a", vendor="Cisco", model="C9300")
    b = Device(hostname="sw-b", vendor="Cisco", model="C9300")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(
        Link(a_device_id=a.id, a_interface="Gi1/0/1", b_device_id=b.id, b_interface="Gi1/0/1")
    )
    db_session.commit()

    report = linkcheck.review(db_session)
    assert report.links_seen == 1
    assert report.links_with_config == 0
    assert any("Config を取り込む" in note for note in report.notes)


def test_the_api_returns_the_report(client, db_session):
    _pair(db_session, {"mode": "trunk"}, {"mode": "access"})
    body = client.get(f"{API}/topology/link-check").json()
    assert body["links_seen"] == 1
    assert body["findings"]
    assert body["findings"][0]["a_hostname"] == "sw-a"
    assert body["findings"][0]["why"]


# ---------------------------------------------------------------------------
# 両端のアドレス
# ---------------------------------------------------------------------------


def test_both_ends_in_different_segments_is_the_heaviest(db_session):
    """同じ線でも、別の区画にいれば直接は通じない。"""
    _pair(
        db_session,
        {"ip_address": "10.0.0.1/30"},
        {"ip_address": "10.0.1.2/30"},
    )
    report = linkcheck.review(db_session)
    assert report.findings[0].finding.title == "両端が別のセグメントです"
    assert report.findings[0].finding.level == HIGH


def test_the_same_address_on_both_ends_is_reported(db_session):
    _pair(db_session, {"ip_address": "10.0.0.1/30"}, {"ip_address": "10.0.0.1/30"})
    assert "両端に同じアドレスが入っています" in _titles(db_session)


def test_a_different_mask_is_reported_even_when_it_works_today(db_session):
    _pair(db_session, {"ip_address": "10.0.0.1/24"}, {"ip_address": "10.0.0.2/30"})
    report = linkcheck.review(db_session)
    assert report.findings[0].finding.title == "両端でセグメントの区切りが違います"
    assert report.findings[0].finding.level == MEDIUM


def test_a_matching_pair_of_addresses_gets_no_complaints(db_session):
    _pair(db_session, {"ip_address": "10.0.0.1/30"}, {"ip_address": "10.0.0.2/30"})
    assert _titles(db_session) == []


def test_nothing_is_said_when_one_end_has_no_address(db_session):
    """L2 の口かもしれないし、取り込んだ Config が抜粋かもしれない。"""
    _pair(db_session, {"ip_address": "10.0.0.1/30"}, {"mode": "access", "vlan": 10})
    assert _titles(db_session) == []
