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
    mapping = detect_mapping(["ホスト名", "hostname", "ベンダー", "型番"])
    assert mapping["ホスト名"] == "hostname"
    assert "hostname" not in mapping


def test_hostname_and_device_name_are_different_columns():
    """ホスト名は機械の名前、機器名は人が付けた呼び名。同じ列にしない。"""
    mapping = detect_mapping(["ホスト名", "機器名"])
    assert mapping["ホスト名"] == "hostname"
    assert mapping["機器名"] == "label"


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


def test_collect_samples_formats_excel_dates_and_dedupes():
    from datetime import datetime

    from app.services.importer import collect_samples

    rows = [
        {"導入日": datetime(2021, 4, 1, 0, 0), "拠点": "本社"},
        {"導入日": datetime(2021, 4, 1, 0, 0), "拠点": "大阪"},
        {"導入日": "", "拠点": "本社"},
    ]
    samples = collect_samples(["導入日", "拠点"], rows)

    assert samples["導入日"] == ["2021/04/01"]  # 重複と空欄は除く
    assert samples["拠点"] == ["本社", "大阪"]


def test_parse_date_fast_path_accepts_common_separators():
    assert parse_date("2024-04-01") == date(2024, 4, 1)
    assert parse_date("2024/04/01") == date(2024, 4, 1)
    assert parse_date("2024.04.01") == date(2024, 4, 1)
    assert parse_date("2024/4") == date(2024, 4, 1)


def test_sample_rows_prefers_errors():
    from app.services.importer import _ROW_SAMPLE, RowAction, RowResult, _sample_rows

    results = [
        RowResult(row_number=i, key=f"sw-{i}", action=RowAction.CREATE)
        for i in range(1, _ROW_SAMPLE + 50)
    ]
    # 後ろの方にエラーを置く。サンプルはエラーを優先して拾う
    results[80].action = RowAction.ERROR
    results[200].action = RowAction.ERROR
    results[201].action = RowAction.ERROR

    sampled = _sample_rows(results)
    assert len(sampled) == _ROW_SAMPLE
    assert [r.action for r in sampled[:3]] == [
        RowAction.ERROR,
        RowAction.ERROR,
        RowAction.ERROR,
    ]


def test_reimport_with_contracts_does_not_query_per_row(db_session):
    """保守契約列がある再取り込みで、行数分の問い合わせにならないこと。

    以前は device.contracts を1台ずつ見に行き、数千行でフリーズしていた。
    """
    from sqlalchemy import event

    from app.services.importer import KeyField, run_import

    headers = [
        "管理ID",
        "ホスト名",
        "メーカー",
        "型番",
        "シリアル番号",
        "種別",
        "状態",
        "設置場所",
        "保守ベンダー",
        "保守終了日",
    ]

    def rows(n: int, end: str = "2027/3/31"):
        return [
            {
                "管理ID": f"A{i:04d}",
                "ホスト名": f"sw-{i:04d}",
                "メーカー": "Cisco",
                "型番": "C9300",
                "シリアル番号": f"SN{i}",
                "種別": "スイッチ",
                "状態": "稼働中",
                "設置場所": "本社",
                "保守ベンダー": "Vendor",
                "保守終了日": end,
            }
            for i in range(n)
        ]

    n = 400
    first = run_import(db_session, headers, rows(n), key_field=KeyField.CODE, commit=True)
    assert first.created == n
    assert first.contracts_added == n

    queries = {"n": 0}
    engine = db_session.get_bind()

    def count(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        queries["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        preview = run_import(
            db_session, headers, rows(n, end="2028/3/31"), key_field=KeyField.CODE, commit=False
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert preview.updated == n
    assert preview.contracts_added == n
    # 行ごとに契約を見に行くと n 回以上増える。塊読みなら数百で足りる
    assert queries["n"] < n, queries["n"]
