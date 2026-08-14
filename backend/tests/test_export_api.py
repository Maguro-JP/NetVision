import csv
import io

from app.services.exporter import _STATUS_LABELS, _TYPE_LABELS
from app.services.importer import (
    DEVICE_STATUS_ALIASES,
    DEVICE_TYPE_ALIASES,
    IMPORTABLE_FIELDS,
    detect_mapping,
    normalize_header,
)
from tests.conftest import API

CSV_HEADER = "ホスト名,メーカー,型式,機器種別,状態,OSバージョン,設置場所,ラック番号,導入年月日,サポート終了日,保守会社,保守開始日,保守期限,年額\n"  # noqa: E501
CSV_ROWS = (
    'core-sw-01,Cisco,C9500-40X,L3SW,稼働中,17.09.04a,本社DC,A-01,2021/4/1,2030/2/25,ネットワン,2024/4/1,2027/3/31,"480,000"\n'  # noqa: E501
    "edge-rt-01,Juniper,MX204,ルータ,撤去済み,21.4R3-S5,大阪支社,,2019年10月1日,2026/9/19,Juniper Care,2023/10/1,2026/10/4,720000\n"  # noqa: E501
)


def seed(client):
    res = client.post(
        f"{API}/import/devices",
        files={"file": ("daichou.csv", (CSV_HEADER + CSV_ROWS).encode("utf-8"), "text/csv")},
    )
    assert res.json()["committed"] is True


def read_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    records = [r for r in reader if any(c.strip() for c in r)]
    headers = records[0]
    return headers, [dict(zip(headers, r, strict=False)) for r in records[1:]]


def test_export_csv_contains_all_devices(client):
    seed(client)
    res = client.get(f"{API}/export/devices.csv")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "netvision_devices_" in res.headers["content-disposition"]
    assert res.content.startswith(b"\xef\xbb\xbf")  # Excel 用の BOM

    headers, rows = read_csv(res.text)
    assert [r["ホスト名"] for r in rows] == ["core-sw-01", "edge-rt-01"]

    core = rows[0]
    assert core["ベンダー"] == "Cisco"
    assert core["型番"] == "C9500-40X"
    assert core["種別"] == "スイッチ"
    assert core["状態"] == "稼働中"
    assert core["拠点"] == "本社DC"
    assert core["導入日"] == "2021/04/01"
    assert core["保守終了日"] == "2027/03/31"
    assert core["保守年額"] == "480000.0"
    assert "アラート" in headers


def test_export_can_omit_calculated_columns(client):
    seed(client)
    headers, _ = read_csv(client.get(f"{API}/export/devices.csv?include_status=false").text)

    assert "アラート" not in headers
    assert "保守期限まで(日)" not in headers
    # 算出列を外すと、取り込み可能な項目だけが残る
    assert len(headers) == len(IMPORTABLE_FIELDS)


def test_exported_csv_can_be_imported_back(client):
    """往復できること。書き出し → 取り込みで、内容が変わらないのを確認する。"""
    seed(client)
    exported = client.get(f"{API}/export/devices.csv").content

    before = client.get(f"{API}/devices").json()

    res = client.post(
        f"{API}/import/devices",
        files={"file": ("exported.csv", exported, "text/csv")},
    )
    body = res.json()
    assert body["committed"] is True
    assert body["created"] == 0
    assert body["updated"] == 2
    assert body["errors"] == 0
    assert body["contracts_added"] == 0  # 同じ契約は重複登録しない

    after = client.get(f"{API}/devices").json()
    assert len(after) == 2
    for old, new in zip(before, after, strict=True):
        for key in ("hostname", "vendor", "model", "device_type", "status", "site_name"):
            assert old[key] == new[key]
        for key in ("install_date", "eol_date", "os_version"):
            assert old[key] == new[key]
        assert old["lifecycle"]["maintenance_end"] == new["lifecycle"]["maintenance_end"]


def test_export_headers_are_recognised_by_the_importer(client):
    """書き出した見出しが、取り込み側でそのまま自動対応付けされること。"""
    seed(client)
    headers, _ = read_csv(client.get(f"{API}/export/devices.csv?include_status=false").text)

    mapping = detect_mapping(headers)
    assert set(mapping.values()) == set(IMPORTABLE_FIELDS)


def test_exported_type_and_status_labels_are_importable():
    """書き出す日本語表記は、必ず取り込み側の別名辞書に含まれていること。"""
    for label in _TYPE_LABELS.values():
        assert normalize_header(label) in DEVICE_TYPE_ALIASES
    for label in _STATUS_LABELS.values():
        assert normalize_header(label) in DEVICE_STATUS_ALIASES


def test_export_respects_filters(client):
    seed(client)

    _, rows = read_csv(client.get(f"{API}/export/devices.csv?vendor=Juniper").text)
    assert [r["ホスト名"] for r in rows] == ["edge-rt-01"]

    _, rows = read_csv(client.get(f"{API}/export/devices.csv?status=retired").text)
    assert [r["ホスト名"] for r in rows] == ["edge-rt-01"]

    _, rows = read_csv(client.get(f"{API}/export/devices.csv?q=C9500").text)
    assert [r["ホスト名"] for r in rows] == ["core-sw-01"]


def test_export_shift_jis(client):
    seed(client)
    res = client.get(f"{API}/export/devices.csv?encoding=shift_jis")

    assert res.status_code == 200
    text = res.content.decode("cp932")
    assert "ホスト名" in text
    assert "core-sw-01" in text


def test_export_rejects_unknown_encoding(client):
    res = client.get(f"{API}/export/devices.csv?encoding=euc-jp")
    assert res.status_code == 422


def test_export_xlsx(client):
    from openpyxl import load_workbook

    seed(client)
    res = client.get(f"{API}/export/devices.xlsx")
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]

    sheet = load_workbook(io.BytesIO(res.content)).active
    assert sheet.title == "機器台帳"
    assert sheet.freeze_panes == "A2"

    headers = [c.value for c in sheet[1]]
    # 先頭は内部ID。書き出して直して戻すとき、この列で同じ機器だと分かる
    assert headers[0] == "内部ID"
    assert headers[1] == "管理ID"
    assert "拠点ID" in headers
    assert "ホスト名" in headers

    values = dict(zip(headers, [c.value for c in sheet[2]], strict=False))
    assert values["ホスト名"] == "core-sw-01"
    assert values["保守年額"] == 480000  # 数値セルとして入る
    assert str(values["導入日"]).startswith("2021-04-01")  # 日付セルとして入る


def test_export_empty_database_returns_headers_only(client):
    headers, rows = read_csv(client.get(f"{API}/export/devices.csv").text)
    assert rows == []
    # 先頭は内部ID。書き出して直して戻すとき、この列で同じ機器だと分かる
    assert headers[0] == "内部ID"
    assert headers[1] == "管理ID"
    assert "拠点ID" in headers
    assert "ホスト名" in headers
