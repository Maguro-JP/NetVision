"""絞り込みを SQL 側で行うようにしたので、判定が今までと一致すること。

重要度は lifecycle.evaluate が正で、device_query の CASE 式はその写しにあたる。
食い違うと「一覧に出ないのに、開いてみると期限切れ」という状態になり、
気づくのは期限が切れたあとになる。全パターンで突き合わせて見張る。
"""

from datetime import date, timedelta

import pytest

from app.config import settings
from app.models.device import Device, DeviceStatus, DeviceType
from app.models.maintenance import MaintenanceContract
from app.models.reference import ProductReference
from app.services.device_query import DeviceFilters, count_devices, query_devices
from app.services.lifecycle import Severity, evaluate

TODAY = date(2026, 8, 10)


def build_fleet(db) -> None:
    """重要度が一巡するように機器を並べる。

    しきい値の境目（ちょうど当日 / critical の端 / warning の端）を必ず含める。
    off-by-one はここでしか出ない。
    """
    offsets = [
        None,  # 日付なし → unknown
        -400,
        -1,
        0,  # 当日は「まだ切れていない」
        1,
        settings.critical_days,
        settings.critical_days + 1,
        settings.warning_days,
        settings.warning_days + 1,
        3000,
    ]

    for index, offset in enumerate(offsets):
        eol = None if offset is None else TODAY + timedelta(days=offset)
        db.add(
            Device(
                hostname=f"eol-{index:02d}",
                vendor="Cisco",
                model=f"MODEL-{index}",
                device_type=DeviceType.SWITCH,
                eol_date=eol,
            )
        )

    # 保守契約の終了日でも重要度が付く。EoL より重ければそちらが総合になる
    for index, offset in enumerate(offsets):
        device = Device(
            hostname=f"contract-{index:02d}",
            vendor="Cisco",
            model=f"CMODEL-{index}",
            device_type=DeviceType.ROUTER,
            eol_date=TODAY + timedelta(days=3000),  # EoL は十分先
        )
        db.add(device)
        db.flush()
        if offset is not None:
            db.add(
                MaintenanceContract(
                    device_id=device.id,
                    provider="保守会社",
                    start_date=TODAY - timedelta(days=1000),
                    end_date=TODAY + timedelta(days=offset),
                )
            )

    # 型番の製品マスタから EoL を引く機器（機器側は空）
    db.add(ProductReference(vendor="NEC", model="IX2215", eol_date=TODAY - timedelta(days=10)))
    db.add(
        Device(
            hostname="from-product",
            vendor="nec",  # 大文字小文字が違っても引けること
            model="ix2215",
            device_type=DeviceType.ROUTER,
        )
    )

    # 撤去済みはアラート対象から外れる
    db.add(
        Device(
            hostname="retired-01",
            vendor="Cisco",
            model="OLD",
            device_type=DeviceType.SWITCH,
            status=DeviceStatus.RETIRED,
            eol_date=TODAY - timedelta(days=500),
        )
    )
    db.commit()


@pytest.mark.parametrize("severity", list(Severity))
def test_sql_filter_agrees_with_evaluate(db_session, severity):
    build_fleet(db_session)

    expected = {
        d.hostname
        for d in query_devices(db_session, DeviceFilters())
        if evaluate(d, TODAY).overall_severity == severity
    }
    actual = {
        d.hostname for d in query_devices(db_session, DeviceFilters(severity=severity), today=TODAY)
    }

    assert actual == expected, f"{severity}: SQL と evaluate が食い違っている"


def test_every_device_lands_in_exactly_one_severity(db_session):
    """どの重要度にも入らない機器があると、一覧から消える。"""
    build_fleet(db_session)
    everything = {d.hostname for d in query_devices(db_session, DeviceFilters())}

    seen: set[str] = set()
    for severity in Severity:
        for device in query_devices(db_session, DeviceFilters(severity=severity), today=TODAY):
            assert device.hostname not in seen, f"{device.hostname} が2つの重要度に出ている"
            seen.add(device.hostname)

    assert seen == everything


def test_retired_devices_are_not_alerted(db_session):
    build_fleet(db_session)
    names = {
        d.hostname
        for d in query_devices(db_session, DeviceFilters(severity=Severity.OK), today=TODAY)
    }
    assert "retired-01" in names


def test_product_reference_supplies_the_eol(db_session):
    """機器に EoL が無くても、型番の製品マスタから引けること。"""
    build_fleet(db_session)
    names = {
        d.hostname
        for d in query_devices(db_session, DeviceFilters(severity=Severity.EXPIRED), today=TODAY)
    }
    assert "from-product" in names


def test_paging_returns_each_device_once(db_session):
    """ページを送って集めると、重複も抜けもなく全件になること。"""
    build_fleet(db_session)
    total = count_devices(db_session, DeviceFilters())

    collected: list[str] = []
    for offset in range(0, total, 7):
        page = query_devices(db_session, DeviceFilters(), limit=7, offset=offset)
        collected.extend(d.hostname for d in page)

    assert len(collected) == total
    assert len(set(collected)) == total


def test_count_matches_the_number_of_rows(db_session):
    build_fleet(db_session)
    for filters in (
        DeviceFilters(),
        DeviceFilters(device_type=DeviceType.ROUTER),
        DeviceFilters(severity=Severity.EXPIRED),
        DeviceFilters(incomplete=True),
    ):
        rows = query_devices(db_session, filters, today=TODAY)
        assert count_devices(db_session, filters, today=TODAY) == len(rows)


def test_incomplete_finds_devices_with_blanks(db_session):
    build_fleet(db_session)
    db_session.add(Device(hostname="blank-01", vendor="", model="", device_type=DeviceType.OTHER))
    db_session.commit()

    names = {d.hostname for d in query_devices(db_session, DeviceFilters(incomplete=True))}
    assert "blank-01" in names
    # 拠点が空の機器も情報不足として拾う（この試験の機器は拠点を入れていない）
    assert len(names) > 1


def test_q_and_column_filters_match_any_field(db_session):
    """全体検索はどの項目でも当たり、列フィルターは部分一致で絞れること。"""
    from app.models.site import Site

    site = Site(name="大阪DC", address="大阪府")
    db_session.add(site)
    db_session.flush()
    db_session.add(
        Device(
            hostname="edge-fw-01",
            vendor="Fortinet",
            model="FG-100F",
            serial_number="FGT123",
            device_type=DeviceType.FIREWALL,
            status=DeviceStatus.ACTIVE,
            os_version="7.2.5",
            mgmt_ip="192.0.2.10",
            rack="A-01",
            notes="境界防火壁",
            site_id=site.id,
            eol_date=TODAY + timedelta(days=400),
        )
    )
    db_session.add(
        Device(
            hostname="core-sw-01",
            vendor="Cisco",
            model="C9500",
            device_type=DeviceType.SWITCH,
            status=DeviceStatus.ACTIVE,
        )
    )
    db_session.commit()

    def names(**kwargs):
        return {d.hostname for d in query_devices(db_session, DeviceFilters(**kwargs), today=TODAY)}

    assert names(q="192.0.2.10") == {"edge-fw-01"}
    assert names(q="大阪") == {"edge-fw-01"}
    assert names(q="ファイアウォール") == {"edge-fw-01"}
    assert names(q="境界") == {"edge-fw-01"}
    assert names(product="FG-") == {"edge-fw-01"}
    assert names(hostname="edge") == {"edge-fw-01"}
    assert names(os_version="7.2") == {"edge-fw-01"}
    assert names(site_name="大阪") == {"edge-fw-01"}
    assert names(eol="2027") == {"edge-fw-01"}


def test_severity_counts_agree_with_evaluate(db_session):
    """ダッシュボードの件数も SQL と evaluate が一致すること。"""
    from collections import Counter

    from app.services.device_query import (
        count_by_eol_severity,
        count_by_overall_severity,
        query_upcoming,
    )

    build_fleet(db_session)
    devices = query_devices(db_session, DeviceFilters())
    overall = Counter(evaluate(d, TODAY).overall_severity.value for d in devices)
    eol = Counter(evaluate(d, TODAY).eol_severity.value for d in devices)

    assert count_by_overall_severity(db_session, today=TODAY) == {
        severity.value: overall.get(severity.value, 0) for severity in Severity
    }
    assert count_by_eol_severity(db_session, today=TODAY) == {
        severity.value: eol.get(severity.value, 0) for severity in Severity
    }

    alerting = [
        d
        for d in devices
        if evaluate(d, TODAY).overall_severity
        in {Severity.EXPIRED, Severity.CRITICAL, Severity.WARNING}
    ]
    alerting.sort(
        key=lambda d: (
            min(
                x
                for x in (
                    evaluate(d, TODAY).days_to_maintenance_end,
                    evaluate(d, TODAY).days_to_eol,
                )
                if x is not None
            ),
            d.hostname,
        )
    )
    assert [d.hostname for d in query_upcoming(db_session, 5, today=TODAY)] == [
        d.hostname for d in alerting[:5]
    ]
