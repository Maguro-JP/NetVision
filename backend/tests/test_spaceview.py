"""眺めるための、拠点単位にまとめた姿。

機器の数だけ大きくなる全体グラフでは、全国規模で開けない。返る大きさが
拠点数で決まることと、まとめても状態を見落とさないことを押さえる。
"""

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link
from app.services import spaceview
from tests.conftest import API


def build(db) -> tuple[int, int]:
    east = Site(name="東拠点")
    west = Site(name="西拠点")
    db.add_all([east, west])
    db.flush()

    def add(hostname: str, site_id: int, **kwargs) -> Device:
        device = Device(
            hostname=hostname,
            vendor="Cisco",
            model="C9300-48P",
            device_type=DeviceType.SWITCH,
            site_id=site_id,
            **kwargs,
        )
        db.add(device)
        return device

    a = add("east-rt-01", east.id)
    add("east-sw-01", east.id)
    add("east-sw-02", east.id)
    b = add("west-rt-01", west.id)
    add("no-site-sw", None)
    db.flush()
    db.add(Link(a_device_id=a.id, a_interface="Gi0/0", b_device_id=b.id, b_interface="Gi0/1"))
    db.commit()
    return east.id, west.id


def test_sites_are_summarised_with_their_device_count(db_session):
    east, _ = build(db_session)
    view = spaceview.build(db_session)

    counts = {point.name: point.device_count for point in view.sites}
    assert counts["東拠点"] == 3
    assert counts["西拠点"] == 1
    assert view.device_total == 5


def test_devices_without_a_site_are_still_shown(db_session):
    """拠点未登録の機器を落とすと、合計が合わなくなって数字が信用されない。"""
    build(db_session)
    view = spaceview.build(db_session)

    assert "拠点未登録" in {point.name for point in view.sites}


def test_links_between_sites_are_counted(db_session):
    east, west = build(db_session)
    view = spaceview.build(db_session)

    assert len(view.edges) == 1
    assert {view.edges[0].a, view.edges[0].b} == {east, west}
    assert view.edges[0].count == 1


def test_links_inside_a_site_are_not_shown(db_session):
    """拠点の中の線は、引いて眺めているときには見えない。"""
    east, _ = build(db_session)
    inside = db_session.query(Device).filter(Device.site_id == east).all()
    db_session.add(
        Link(
            a_device_id=inside[0].id,
            a_interface="Gi1/0/1",
            b_device_id=inside[1].id,
            b_interface="Gi1/0/2",
        )
    )
    db_session.commit()

    view = spaceview.build(db_session)
    assert len(view.edges) == 1  # 増えない


def test_the_worst_state_in_a_site_is_kept(db_session):
    """まとめたせいで期限切れを見落とさないこと。"""
    from datetime import date, timedelta

    east, _ = build(db_session)
    device = db_session.query(Device).filter(Device.site_id == east).first()
    device.eol_date = date.today() - timedelta(days=100)
    db_session.commit()

    view = spaceview.build(db_session)
    east_point = next(point for point in view.sites if point.name == "東拠点")
    assert east_point.severity == "expired"


def test_endpoint_returns_the_summary(client, db_session):
    build(db_session)
    body = client.get(f"{API}/topology/space").json()

    assert body["device_total"] == 5
    assert len(body["sites"]) == 3  # 東・西・拠点未登録
    assert body["edges"]
