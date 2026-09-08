"""型番の飾りが違っても、製品リファレンスの EoL が機器に当たること。

Cisco は同じハードを `WS-C2960X-48FPD-L` とも `C2960X-48FPD-L` とも印字する。
型番の文字列で結合すると、EoL を入れたのに機器に当たらない。飾りを外した鍵
（model_key）で結合する。他のベンダーには Cisco の規則を当てはめない。
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.models.device import Device, DeviceType
from app.models.reference import ProductReference
from app.services import versions
from app.services.device_query import DeviceFilters, query_devices
from app.services.lifecycle import evaluate

TODAY = date(2026, 9, 8)


def test_model_key_strips_only_cisco_decorations() -> None:
    assert versions.model_key("Cisco", "WS-C2960X-48FPD-L") == versions.model_key(
        "cisco", "C2960X-48FPD-L"
    )
    assert versions.model_key("Cisco", "C9300-48P-A") == versions.model_key("Cisco", "C9300-48P")
    assert versions.model_key("Cisco", "ISR4331/K9") == versions.model_key("Cisco", "ISR4331")
    # 別のハードは別の鍵のまま
    assert versions.model_key("Cisco", "C9300-48P") != versions.model_key("Cisco", "C9300-48U")
    # 他社の型番には Cisco の規則を当てない（末尾の -L をライセンスの束と見なさない）
    assert versions.model_key("Allied Telesis", "AT-x230-28GP-L") != versions.model_key(
        "Allied Telesis", "AT-x230-28GP"
    )


def test_the_eol_of_the_product_reaches_the_printed_model(db_session) -> None:
    db = db_session
    db.add(
        ProductReference(vendor="Cisco", model="C2960X-48FPD-L", eol_date=TODAY - timedelta(days=1))
    )
    db.add(ProductReference(vendor="Cisco", model="C9300-48P", eol_date=TODAY + timedelta(days=30)))
    db.add(
        Device(
            hostname="sw-2960x",
            vendor="Cisco",
            model="WS-C2960X-48FPD-L",
            device_type=DeviceType.SWITCH,
        )
    )
    db.add(
        Device(
            hostname="sw-9300", vendor="cisco", model="C9300-48P-A", device_type=DeviceType.SWITCH
        )
    )
    db.add(
        Device(
            hostname="sw-9300u", vendor="Cisco", model="C9300-48U-A", device_type=DeviceType.SWITCH
        )
    )
    db.commit()

    by_name = {d.hostname: d for d in db.scalars(select(Device))}
    assert by_name["sw-2960x"].model_key == "c2960x48fpd"
    assert by_name["sw-2960x"].product is not None
    old = evaluate(by_name["sw-2960x"], today=TODAY)
    assert old.effective_eol_date == TODAY - timedelta(days=1)
    soon = evaluate(by_name["sw-9300"], today=TODAY)
    assert soon.effective_eol_date == TODAY + timedelta(days=30)
    # 48U に 48P の EoL を当てない
    assert by_name["sw-9300u"].product is None

    # SQL 側（一覧の並び順・重要度）も同じ鍵で引くこと
    rows = query_devices(db, DeviceFilters(), today=TODAY)
    found = {d.hostname: d for d in rows}
    assert found["sw-2960x"].product is not None


def test_changing_the_model_refreshes_the_key(db_session) -> None:
    db = db_session
    device = Device(
        hostname="r1", vendor="Cisco", model="ISR4331/K9", device_type=DeviceType.ROUTER
    )
    db.add(device)
    db.commit()
    assert device.model_key == "isr4331"
    device.model = "ISR4351/K9"
    db.commit()
    assert device.model_key == "isr4351"
