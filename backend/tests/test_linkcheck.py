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


# ---------------------------------------------------------------------------
# OSPF
# ---------------------------------------------------------------------------


def test_a_matching_ospf_pair_is_quiet(db_session):
    values = {
        "ip_address": "10.0.0.1/30",
        "ospf_area": "0",
        "ospf_hello": 10,
        "ospf_dead": 40,
        "ospf_network": "point-to-point",
    }
    _pair(db_session, dict(values), {**values, "ip_address": "10.0.0.2/30"})
    assert _titles(db_session) == []


def test_a_different_area_is_reported(db_session):
    _pair(
        db_session,
        {"ospf_area": "0", "ospf_hello": 10},
        {"ospf_area": "1", "ospf_hello": 10},
    )
    report = linkcheck.review(db_session)
    assert report.findings[0].finding.title == "OSPF のエリアが両端で違います"
    assert report.findings[0].finding.level == HIGH


def test_a_different_hello_is_reported(db_session):
    _pair(db_session, {"ospf_area": "0", "ospf_hello": 5}, {"ospf_area": "0", "ospf_hello": 10})
    assert "OSPF のあいさつの間隔（hello）が両端で違います" in _titles(db_session)


def test_authentication_on_only_one_side_is_reported(db_session):
    _pair(
        db_session,
        {"ospf_area": "0", "ospf_auth": "md5"},
        {"ospf_area": "0"},
    )
    assert "OSPF の合言葉の掛け方が両端で違います" in _titles(db_session)


def test_nothing_is_said_when_only_one_side_runs_ospf(db_session):
    """片側だけ静的経路にしてあることがある。そこは意図として扱う。"""
    _pair(db_session, {"ospf_area": "0", "ospf_hello": 10}, {"mode": "access", "vlan": 10})
    assert _titles(db_session) == []


def test_only_one_side_being_shut_down_is_the_heaviest(db_session):
    """図の上ではつながって見えるのに通らない。いちばん気づきにくい形。"""
    _pair(
        db_session,
        {"mode": "trunk", "enabled": True},
        {"mode": "trunk", "enabled": False},
    )
    titles = _titles(db_session)
    assert "片側だけポートが落ちています" in titles


def test_both_sides_shut_down_is_left_alone(db_session):
    """両端とも落としてあるなら、まだ使っていない線。直すものではない。"""
    _pair(
        db_session,
        {"mode": "trunk", "enabled": False},
        {"mode": "trunk", "enabled": False},
    )
    assert "片側だけポートが落ちています" not in _titles(db_session)


def test_it_says_how_many_links_it_could_not_look_at(db_session):
    """見ていない線があるのに指摘が0件だと、全部そろっていると読まれる。

    「食い違いが無い」と「まだ見ていない」は違う。
    """
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "10,20"},
        {"mode": "trunk", "trunk_vlans": "10,20"},
    )
    # 片端の Config が無い線をもう1本足す
    lonely = Device(hostname="sw-c", vendor="Cisco", model="C9300")
    db_session.add(lonely)
    db_session.flush()
    known = db_session.query(Device).filter(Device.hostname == "sw-a").one()
    db_session.add(
        Link(
            a_device_id=known.id,
            a_interface="GigabitEthernet1/0/2",
            b_device_id=lonely.id,
            b_interface="GigabitEthernet1/0/2",
        )
    )
    db_session.commit()

    report = linkcheck.review(db_session)
    assert report.links_seen == 2
    assert report.links_with_config == 1
    assert any("2 本のうち 1 本" in note for note in report.notes)


def test_nothing_is_said_when_every_link_was_looked_at(db_session):
    _pair(
        db_session,
        {"mode": "trunk", "trunk_vlans": "10,20"},
        {"mode": "trunk", "trunk_vlans": "10,20"},
    )
    report = linkcheck.review(db_session)
    assert report.links_with_config == report.links_seen
    assert not any("見ていません" in note for note in report.notes)


def test_speed_auto_written_out_is_the_same_as_not_written(db_session):
    """`speed auto` / `duplex auto` は既定の自動と同じ。

    書いていない側と比べて「片側だけ固定」と言わない。

    出典: Cisco「Cisco IOS XE 17 - Interface Characteristics Configuration Guide」の
    Default Ethernet interface configuration（Speed: Autonegotiate、Duplex mode:
    Autonegotiate）と Set the interface speed and duplex parameters（`speed ... auto`、
    `duplex {auto | full | half}`）。
    """
    _pair(db_session, {"speed": "auto", "duplex": "auto"}, {})
    titles = " / ".join(_titles(db_session))
    assert "片側だけ速度を固定" not in titles
    assert "片側だけ二重を固定" not in titles


def test_ospf_authentication_null_is_no_authentication():
    """`ip ospf authentication null` は、認証を掛けない指定（エリアの指定を打ち消す）。

    出典: Cisco「Cisco Nexus 6000 Series NX-OS Unicast Routing Command Reference」の ip ospf
    authentication（null: 「Specifies that no authentication is used. Use this keyword to override
    any other authentication configured for an area.」）と技術資料「Configure Authentication in
    Open Shortest Path First」（Null Authentication は Type 0、既定）。
    """
    from app.services import configparse

    text = (
        "hostname r1\n!\ninterface GigabitEthernet0/1\n ip address 10.0.0.1 255.255.255.252\n"
        " ip ospf authentication null\n ip ospf 1 area 0\n!\n"
    )
    [port] = [i for i in configparse.parse(text).interfaces if i.name == "GigabitEthernet0/1"]
    assert port.ospf_auth == "none"
