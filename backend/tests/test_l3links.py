"""経路の情報から結線を推定する。

現場から出てくるのは running-config だけということが多い。LLDP を切って
いる現場もある。それでも L3 の情報は必ず書いてあるので、そこから引ける
線を出す。ただし「たぶんこう」を勝手に台帳へ書かない。
"""

from app.models import Device
from app.models.topology import Interface, Link, LinkOrigin
from app.services import l3links
from tests.conftest import API


def _device(db, hostname: str, *ports: tuple[str, str]) -> Device:
    device = Device(hostname=hostname, vendor="Cisco", model="C9500-40X")
    db.add(device)
    db.flush()
    for name, address in ports:
        db.add(
            Interface(
                device_id=device.id,
                name=name,
                normalized_name=name.lower(),
                ip_address=address,
            )
        )
    db.flush()
    return device


def test_a_point_to_point_pair_is_stated_plainly(db_session):
    """/30 は2台しか入らない。そこに2台いれば直結と言い切れる。"""
    _device(db_session, "core-rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    _device(db_session, "edge-rt-01", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.commit()

    found = l3links.candidates(db_session)
    assert len(found) == 1
    assert found[0].confidence == l3links.HIGH
    assert found[0].segment == "10.0.0.0/30"
    assert "直結" in found[0].why


def test_a_wider_segment_with_two_devices_is_offered_with_a_caveat(db_session):
    """つながってはいるが、間にスイッチが挟まっている可能性がある。"""
    _device(db_session, "core-sw-01", ("Vlan10", "10.10.10.1/24"))
    _device(db_session, "dist-sw-01", ("Vlan10", "10.10.10.2/24"))
    db_session.commit()

    found = l3links.candidates(db_session)
    assert found[0].confidence == l3links.MEDIUM
    assert "挟まっている" in found[0].why


def test_a_crowded_segment_gives_no_line(db_session):
    """3台以上いると、誰と誰が直結なのかは決まらない。"""
    for index in range(3):
        _device(db_session, f"sw-0{index}", ("Vlan10", f"10.10.10.{index + 1}/24"))
    db_session.commit()

    assert l3links.candidates(db_session) == []


def test_a_device_alone_in_its_segment_gives_no_line(db_session):
    _device(db_session, "sw-01", ("Vlan10", "10.10.10.1/24"))
    db_session.commit()
    assert l3links.candidates(db_session) == []


def test_a_pair_that_already_has_a_line_is_not_offered_again(db_session):
    left = _device(db_session, "core-rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    right = _device(db_session, "edge-rt-01", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.add(
        Link(
            a_device_id=left.id,
            a_interface="GigabitEthernet0/0",
            b_device_id=right.id,
            b_interface="GigabitEthernet0/0",
        )
    )
    db_session.commit()

    assert l3links.candidates(db_session) == []


def test_the_sure_ones_come_first(db_session):
    _device(db_session, "aaa-sw-01", ("Vlan10", "10.10.10.1/24"))
    _device(db_session, "bbb-sw-01", ("Vlan10", "10.10.10.2/24"))
    _device(db_session, "zzz-rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    _device(db_session, "zzz-rt-02", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.commit()

    found = l3links.candidates(db_session)
    assert [c.confidence for c in found] == [l3links.HIGH, l3links.MEDIUM]


def test_a_loopback_is_not_a_segment(db_session):
    """/32 は自分1つぶんで、相手のいるセグメントではない。"""
    _device(db_session, "rt-01", ("Loopback0", "10.255.0.1/32"))
    _device(db_session, "rt-02", ("Loopback0", "10.255.0.2/32"))
    db_session.commit()
    assert l3links.candidates(db_session) == []


def test_applying_takes_only_the_sure_ones_by_default(db_session):
    """迷ったものまで一括で入れない。確かでない線が混ざると害のほうが大きい。"""
    _device(db_session, "aaa-sw-01", ("Vlan10", "10.10.10.1/24"))
    _device(db_session, "bbb-sw-01", ("Vlan10", "10.10.10.2/24"))
    _device(db_session, "zzz-rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    _device(db_session, "zzz-rt-02", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.commit()

    created = l3links.apply(db_session)
    assert created == 1
    link = db_session.query(Link).one()
    assert link.origin is LinkOrigin.INFERRED
    assert link.label == "10.0.0.0/30"


def test_a_chosen_pair_can_be_applied_even_when_unsure(db_session):
    left = _device(db_session, "aaa-sw-01", ("Vlan10", "10.10.10.1/24"))
    right = _device(db_session, "bbb-sw-01", ("Vlan10", "10.10.10.2/24"))
    db_session.commit()

    created = l3links.apply(db_session, [(left.id, right.id)])
    assert created == 1


def test_applying_twice_does_not_double_the_line(db_session):
    _device(db_session, "rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    _device(db_session, "rt-02", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.commit()

    assert l3links.apply(db_session) == 1
    assert l3links.apply(db_session) == 0
    assert db_session.query(Link).count() == 1


def test_the_api_lists_and_applies(client, db_session):
    _device(db_session, "rt-01", ("GigabitEthernet0/0", "10.0.0.1/30"))
    _device(db_session, "rt-02", ("GigabitEthernet0/0", "10.0.0.2/30"))
    db_session.commit()

    listed = client.get(f"{API}/topology/link-candidates").json()
    assert len(listed) == 1
    assert listed[0]["evidence"]

    applied = client.post(f"{API}/topology/link-candidates/apply", json={})
    assert applied.status_code == 200
    assert applied.json()["created"] == 1
    assert client.get(f"{API}/topology/link-candidates").json() == []
