from datetime import date

import pytest

from app.models import DeviceStatus, DeviceType
from app.services.importer import (
    CellValueError,
    detect_mapping,
    parse_date,
    parse_decimal,
    parse_device_status,
    parse_device_type,
)


def test_detect_mapping_handles_japanese_headers():
    headers = ["ホスト名", "メーカー", "型番", "導入年月日", "保守終了日", "設置場所"]
    assert detect_mapping(headers) == {
        "ホスト名": "hostname",
        "メーカー": "vendor",
        "型番": "model",
        "導入年月日": "install_date",
        "保守終了日": "contract_end_date",
        "設置場所": "site",
    }


def test_detect_mapping_normalizes_case_and_symbols():
    headers = ["Host Name", "VENDOR", "Model_Number", "管理 IP", "ＯＳバージョン"]
    mapping = detect_mapping(headers)
    assert mapping["Host Name"] == "hostname"
    assert mapping["VENDOR"] == "vendor"
    assert mapping["Model_Number"] == "model"
    assert mapping["管理 IP"] == "mgmt_ip"
    assert mapping["ＯＳバージョン"] == "os_version"


def test_detect_mapping_keeps_first_column_for_duplicate_fields():
    mapping = detect_mapping(["ホスト名", "機器名", "ベンダー", "型番"])
    assert mapping["ホスト名"] == "hostname"
    assert "機器名" not in mapping


def test_unknown_columns_are_not_mapped():
    assert detect_mapping(["社内管理番号", "担当者"]) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-04-01", date(2024, 4, 1)),
        ("2024/4/1", date(2024, 4, 1)),
        ("2024.04.01", date(2024, 4, 1)),
        ("2024年4月1日", date(2024, 4, 1)),
        ("2024/04", date(2024, 4, 1)),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_accepts_common_japanese_formats(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(CellValueError):
        parse_date("未定")


def test_parse_decimal_strips_currency_symbols():
    assert parse_decimal("¥480,000") == 480000
    assert parse_decimal("480000円") == 480000
    assert parse_decimal("") is None
    with pytest.raises(CellValueError):
        parse_decimal("応相談")


def test_parse_device_type_and_status_accept_japanese():
    assert parse_device_type("スイッチ") is DeviceType.SWITCH
    assert parse_device_type("L3SW") is DeviceType.SWITCH
    assert parse_device_type("FW") is DeviceType.FIREWALL
    assert parse_device_status("稼動中") is DeviceStatus.ACTIVE
    assert parse_device_status("撤去済み") is DeviceStatus.RETIRED
    with pytest.raises(CellValueError):
        parse_device_type("なにか")
