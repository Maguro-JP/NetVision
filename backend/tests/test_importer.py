from datetime import date

import pytest
from sqlalchemy import select

from app.models import Device, DeviceStatus, DeviceType
from app.services.importer import (
    CellValueError,
    KeyField,
    detect_mapping,
    parse_date,
    parse_decimal,
    parse_device_status,
    parse_device_type,
    run_import,
)
from app.services.tablefile import read_table


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


@pytest.mark.parametrize("raw", ["4/1/2026", "01/02/2026"])
def test_parse_date_refuses_when_day_and_month_could_be_swapped(raw):
    """月/日/年（英語設定の Excel）とも日/月/年とも読める値を、片方に決めつけない。

    以前は日/月/年として読み、4/1/2026（4月1日のつもり）が1月4日になっていた。
    """
    with pytest.raises(CellValueError, match="年/月/日"):
        parse_date(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13/4/2026", date(2026, 4, 13)),
        ("4/13/2026", date(2026, 4, 13)),
        ("5/5/2026", date(2026, 5, 5)),
    ],
)
def test_parse_date_reads_the_year_last_form_when_it_is_clear(raw, expected):
    assert parse_date(raw) == expected


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


def test_two_sites_can_share_a_name_when_they_have_ids(db_session):
    """同じ名前の拠点が並んでいても取り込める。

    離れた場所に同じ名前の拠点があること自体は珍しくない（同名のビル、
    同名の営業所）。拠点IDで区別できているのに、名前の重複禁止で
    16万台の台帳が丸ごと入らなくなっていた。
    """
    from app.models import Site
    from app.services.importer import KeyField, run_import

    headers = ["管理ID", "ホスト名", "拠点ID（任意）", "拠点名"]
    rows = [
        {"管理ID": "A0001", "ホスト名": "sw-01", "拠点ID（任意）": "S-001", "拠点名": "大手町ビル"},
        {"管理ID": "A0002", "ホスト名": "sw-02", "拠点ID（任意）": "S-002", "拠点名": "大手町ビル"},
    ]

    outcome = run_import(db_session, headers, rows, key_field=KeyField.CODE, commit=True)

    assert outcome.created == 2
    sites = db_session.query(Site).filter(Site.name == "大手町ビル").all()
    assert {site.code for site in sites} == {"S-001", "S-002"}


def test_renaming_a_site_does_not_collide_with_another(db_session):
    """拠点IDが同じなら名前を書き換える。書き換えた先の名前が
    別の拠点で使われていても止まらない。"""
    from app.models import Site
    from app.services.importer import KeyField, run_import

    db_session.add_all(
        [
            Site(code="S-001", name="旧・大手町"),
            Site(code="S-002", name="大手町ビル"),
        ]
    )
    db_session.commit()

    headers = ["管理ID", "ホスト名", "拠点ID（任意）", "拠点名"]
    rows = [
        {"管理ID": "A0001", "ホスト名": "sw-01", "拠点ID（任意）": "S-001", "拠点名": "大手町ビル"}
    ]

    run_import(db_session, headers, rows, key_field=KeyField.CODE, commit=True)

    renamed = db_session.query(Site).filter(Site.code == "S-001").one()
    assert renamed.name == "大手町ビル"


def test_the_same_vendor_written_differently_is_counted_once(db_session):
    """台帳は人が書くので Cisco / cisco / CISCO が混ざる。

    分かれて数えられると、メーカー別の台数も、そこから引く保守費の
    見積もりも割れる。書き方のゆれだけをそろえる。
    """
    csv = (
        "ホスト名,メーカー,型番\n"
        "vend-sw-01,Cisco,C9200L-24T-4G\n"
        "vend-sw-02,cisco,C9200L-24T-4G\n"
        "vend-sw-03,CISCO,C9200L-24T-4G\n"
        "vend-rt-01,ｎｅｃ風の知らない名前,X\n"
    ).encode()
    table = read_table("ledger.csv", csv)
    run_import(
        db_session,
        table.headers,
        table.rows,
        key_field=KeyField.HOSTNAME,
        commit=True,
    )
    devices = {
        d.hostname: d.vendor
        for d in db_session.scalars(select(Device).where(Device.hostname.like("vend-%")))
    }
    assert devices["vend-sw-01"] == "Cisco"
    assert devices["vend-sw-02"] == "Cisco"
    assert devices["vend-sw-03"] == "Cisco"
    # 知らない名前は書き換えない。こちらで決められることではない
    assert devices["vend-rt-01"] == "ｎｅｃ風の知らない名前"


def test_japanese_era_dates_are_read():
    """台帳、とくに官公庁や古い社内様式では和暦がふつうに出てくる。

    読めないとその行がまるごと取り込めない。保守期限が和暦で書いてある
    だけで、その機器が期限の判定から漏れる。
    """
    assert parse_date("令和5年4月1日") == date(2023, 4, 1)
    assert parse_date("令和元年4月1日") == date(2019, 4, 1)
    assert parse_date("R10.3.31") == date(2028, 3, 31)
    assert parse_date("H31/3/31") == date(2019, 3, 31)
    assert parse_date("平成31年3月31日") == date(2019, 3, 31)
    assert parse_date("S60.1.1") == date(1985, 1, 1)
    # 年月までなら月初として扱う（西暦のときと同じ）
    assert parse_date("R5/4") == date(2023, 4, 1)


def test_an_era_date_that_cannot_exist_is_not_guessed():
    """読めないものを、当てずっぽうで埋めない。"""
    for text in ("令和99年13月", "令和0年1月1日", "ただの文字"):
        with pytest.raises(CellValueError):
            parse_date(text)


def test_common_ways_of_writing_the_status_are_read():
    """台帳の書き方は現場ごとに違う。読めないと、その行が取り込めない。"""
    for text in ("稼働", "稼動", "運用", "現用", "本番"):
        assert parse_device_status(text) is DeviceStatus.ACTIVE, text
    assert parse_device_status("待機中") is DeviceStatus.STANDBY
    assert parse_device_status("保守") is DeviceStatus.MAINTENANCE
    for text in ("廃棄済", "返却"):
        assert parse_device_status(text) is DeviceStatus.RETIRED, text


def test_an_ambiguous_status_is_not_guessed():
    """「停止」は、一時的に止めているのか外したのかが読めない。

    撤去として扱うと、その機器が期限の判定から静かに消える。
    """
    with pytest.raises(CellValueError):
        parse_device_status("停止")
