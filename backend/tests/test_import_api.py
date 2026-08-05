import io
import json

from tests.conftest import API

CSV_HEADER = "ホスト名,メーカー,型番,種別,状態,OSバージョン,設置場所,導入年月日,サポート終了日,保守ベンダー,保守開始日,保守終了日,保守年額\n"  # noqa: E501
CSV_ROWS = (
    'core-sw-01,Cisco,C9500-40X,スイッチ,稼働中,17.09.04a,本社DC,2021/4/1,2030/2/25,ネットワン,2024/4/1,2027/3/31,"480,000"\n'  # noqa: E501
    "edge-rt-01,Juniper,MX204,ルータ,稼働中,21.4R3-S5,本社DC,2019年10月1日,2026/9/19,Juniper Care,2023/10/1,2026/10/4,720000\n"  # noqa: E501
)


def upload(client, body: str, path: str = "/import/devices/preview", **data):
    return client.post(
        f"{API}{path}",
        files={"file": ("daichou.csv", body.encode("utf-8"), "text/csv")},
        data=data,
    )


def test_preview_does_not_write_to_db(client):
    res = upload(client, CSV_HEADER + CSV_ROWS)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["committed"] is False
    assert body["total_rows"] == 2
    assert body["created"] == 2
    assert body["errors"] == 0
    assert body["contracts_added"] == 2
    assert body["mapping"]["ホスト名"] == "hostname"
    assert body["mapping"]["保守終了日"] == "contract_end_date"
    assert [r["action"] for r in body["rows"]] == ["create", "create"]

    # DB には反映されていない
    assert client.get(f"{API}/devices").json() == []


def test_commit_creates_devices_sites_and_contracts(client):
    res = upload(client, CSV_HEADER + CSV_ROWS, path="/import/devices")
    assert res.status_code == 200, res.text
    assert res.json()["committed"] is True

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["core-sw-01", "edge-rt-01"]

    core = next(d for d in devices if d["hostname"] == "core-sw-01")
    assert core["device_type"] == "switch"
    assert core["install_date"] == "2021-04-01"
    assert core["eol_date"] == "2030-02-25"
    assert core["site_name"] == "本社DC"
    assert core["lifecycle"]["maintenance_end"] == "2027-03-31"

    detail = client.get(f"{API}/devices/{core['id']}").json()
    assert detail["contracts"][0]["provider"] == "ネットワン"
    assert detail["contracts"][0]["annual_cost"] == 480000

    # 拠点は台帳の名前から自動作成される
    sites = client.get(f"{API}/sites").json()
    assert [s["name"] for s in sites] == ["本社DC"]
    assert sites[0]["device_count"] == 2


def test_reimport_updates_and_does_not_duplicate_contracts(client):
    upload(client, CSV_HEADER + CSV_ROWS, path="/import/devices")

    updated = CSV_HEADER + (
        "core-sw-01,Cisco,C9500-40X,スイッチ,稼働中,17.12.03,本社DC,2021/4/1,2030/2/25,"
        "ネットワン,2024/4/1,2027/3/31,480000\n"
    )
    body = upload(client, updated, path="/import/devices").json()

    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["contracts_added"] == 0

    devices = client.get(f"{API}/devices").json()
    assert len(devices) == 2
    core = next(d for d in devices if d["hostname"] == "core-sw-01")
    assert core["os_version"] == "17.12.03"
    assert len(client.get(f"{API}/devices/{core['id']}").json()["contracts"]) == 1


def test_blank_cells_do_not_erase_existing_values(client):
    upload(client, CSV_HEADER + CSV_ROWS, path="/import/devices")

    # OSバージョンと拠点が空の台帳を再取り込みしても既存値は消えない
    sparse = "ホスト名,メーカー,型番,OSバージョン,設置場所\ncore-sw-01,Cisco,C9500-40X,,\n"
    assert upload(client, sparse, path="/import/devices").json()["updated"] == 1

    core = next(d for d in client.get(f"{API}/devices").json() if d["hostname"] == "core-sw-01")
    assert core["os_version"] == "17.09.04a"
    assert core["site_name"] == "本社DC"


def test_update_existing_false_skips_known_devices(client):
    upload(client, CSV_HEADER + CSV_ROWS, path="/import/devices")
    body = upload(
        client, CSV_HEADER + CSV_ROWS, path="/import/devices", update_existing="false"
    ).json()

    assert body["skipped"] == 2
    assert body["updated"] == 0


def test_row_errors_block_the_whole_import(client):
    broken = (
        CSV_HEADER + CSV_ROWS + "acc-sw-01,Cisco,C2960X,スイッチ,稼働中,15.2,本社DC,未定,,,,,\n"
    )
    body = upload(client, broken, path="/import/devices").json()

    assert body["committed"] is False
    assert body["errors"] == 1
    error_row = next(r for r in body["rows"] if r["action"] == "error")
    assert error_row["row_number"] == 4
    assert "日付として解釈できません" in error_row["issues"][0]["message"]

    # 1行でもエラーがあれば正常行も取り込まない
    assert client.get(f"{API}/devices").json() == []


def test_missing_required_column_is_reported(client):
    body = upload(client, "ホスト名,設置場所\ncore-sw-01,本社DC\n").json()
    assert body["missing_required_fields"] == ["vendor", "model"]
    assert body["rows"] == []


def test_duplicate_keys_in_one_file_are_rejected(client):
    duplicated = CSV_HEADER + CSV_ROWS + CSV_ROWS.splitlines()[0] + "\n"
    body = upload(client, duplicated).json()
    assert body["errors"] == 1
    assert "重複" in body["rows"][2]["issues"][0]["message"]


def test_mapping_override_wins_over_detection(client):
    csv_text = "装置ID,メーカー,型番,社内コード\nsw-99,Cisco,C9200,ABC\n"
    mapping = json.dumps({"装置ID": "hostname", "社内コード": "serial_number"})
    body = upload(client, csv_text, path="/import/devices", mapping=mapping).json()

    assert body["created"] == 1
    device = client.get(f"{API}/devices").json()[0]
    assert device["hostname"] == "sw-99"
    assert device["serial_number"] == "ABC"


def test_mapping_can_disable_a_column(client):
    csv_text = "ホスト名,メーカー,型番,備考\nsw-98,Cisco,C9200,取り込まないメモ\n"
    body = upload(client, csv_text, path="/import/devices", mapping=json.dumps({"備考": ""})).json()

    assert body["unmapped_columns"] == ["備考"]
    assert client.get(f"{API}/devices").json()[0]["notes"] is None


def test_import_by_serial_number_key(client):
    csv_text = "ホスト名,メーカー,型番,シリアル番号\nold-name,Cisco,C9200,SN-001\n"
    upload(client, csv_text, path="/import/devices", key_field="serial_number")

    renamed = "ホスト名,メーカー,型番,シリアル番号\nnew-name,Cisco,C9200,SN-001\n"
    body = upload(client, renamed, path="/import/devices", key_field="serial_number").json()

    assert body["updated"] == 1
    devices = client.get(f"{API}/devices").json()
    assert len(devices) == 1
    assert devices[0]["hostname"] == "new-name"


def test_shift_jis_csv_is_accepted(client):
    res = client.post(
        f"{API}/import/devices/preview",
        files={"file": ("daichou.csv", (CSV_HEADER + CSV_ROWS).encode("cp932"), "text/csv")},
    )
    assert res.status_code == 200
    assert res.json()["created"] == 2


def test_excel_file_is_accepted(client):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ホスト名", "ベンダー", "型番", "導入日"])
    sheet.append(["xl-sw-01", "Cisco", "C9200", "2022/05/10"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    res = client.post(
        f"{API}/import/devices",
        files={
            "file": (
                "daichou.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    assert client.get(f"{API}/devices").json()[0]["install_date"] == "2022-05-10"


def test_unsupported_extension_is_rejected(client):
    res = client.post(
        f"{API}/import/devices/preview",
        files={"file": ("config.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert res.status_code == 422
    assert "対応していない拡張子" in res.json()["detail"]


def test_fields_and_template_endpoints(client):
    fields = client.get(f"{API}/import/fields").json()
    assert {f["name"] for f in fields} >= {"hostname", "vendor", "model", "contract_end_date"}
    assert [f["required"] for f in fields[:3]] == [True, True, True]

    res = client.get(f"{API}/import/template.csv")
    assert res.status_code == 200
    assert res.text.startswith("﻿")
    assert "ホスト名" in res.text


def test_excel_without_extension_is_detected_by_content(client):
    """拡張子が付かない状態で保存されても、中身が xlsx なら読める。"""
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["ホスト名", "ベンダー", "型番"])
    workbook.active.append(["no-ext-01", "Cisco", "C9200"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    res = client.post(
        f"{API}/import/devices/preview",
        files={"file": ("download", buffer.getvalue(), "application/octet-stream")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
