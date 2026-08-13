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

    required = {f["name"] for f in fields if f["required"]}
    assert required == {"hostname", "vendor", "model"}
    # 機器IDは任意。新しく登録する行では空のままでよい
    assert "device_id" in {f["name"] for f in fields}
    assert "device_id" not in required

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


def test_error_rows_can_be_downloaded_and_fixed(client):
    broken = (
        CSV_HEADER
        + CSV_ROWS
        + (
            "acc-sw-01,Cisco,C2960X,スイッチ,稼働中,15.2,本社DC,未定,,,,,\n"
            ",Cisco,C9200,スイッチ,稼働中,17.6,本社DC,2023/4/1,,,,,\n"
        )
    )

    res = client.post(
        f"{API}/import/devices/errors.csv",
        files={"file": ("daichou.csv", broken.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    assert "netvision_import_errors.csv" in res.headers["content-disposition"]

    lines = res.text.lstrip("﻿").strip().splitlines()
    header = lines[0].split(",")
    assert header[:2] == ["ホスト名", "メーカー"]
    assert header[-1] == "エラー内容"

    # エラー行だけが出る（正常な2行は含まれない）
    assert len(lines) == 3
    assert "acc-sw-01" in lines[1]
    assert "日付として解釈できません" in lines[1]
    assert "ホスト名が空です" in lines[2]

    # 直したファイルを取り込み直せる形になっていること
    fixed = res.text.lstrip("﻿").replace("未定", "2023/4/1").replace("エラー内容", "備考メモ")
    fixed_lines = fixed.strip().splitlines()
    repaired = "\n".join([fixed_lines[0], fixed_lines[1]]) + "\n"
    body = upload(client, repaired, path="/import/devices").json()
    assert body["committed"] is True
    assert body["created"] == 1


def test_error_rows_csv_is_empty_when_no_errors(client):
    res = client.post(
        f"{API}/import/devices/errors.csv",
        files={"file": ("daichou.csv", (CSV_HEADER + CSV_ROWS).encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    assert len(res.text.strip().splitlines()) == 1  # ヘッダのみ


def test_error_rows_csv_shift_jis(client):
    broken = CSV_HEADER + "acc-sw-01,Cisco,C2960X,スイッチ,稼働中,15.2,本社DC,未定,,,,,\n"
    res = client.post(
        f"{API}/import/devices/errors.csv",
        files={"file": ("daichou.csv", broken.encode("utf-8"), "text/csv")},
        data={"encoding": "shift_jis"},
    )
    assert res.status_code == 200
    assert "エラー内容" in res.content.decode("cp932")


def test_error_rows_csv_does_not_write_to_db(client):
    broken = CSV_HEADER + CSV_ROWS
    client.post(
        f"{API}/import/devices/errors.csv",
        files={"file": ("daichou.csv", broken.encode("utf-8"), "text/csv")},
    )
    assert client.get(f"{API}/devices").json() == []


def _csv(rows: list[str]) -> bytes:
    return "\n".join(rows).encode("utf-8")


def _import(client, body: bytes, **data):
    return client.post(
        f"{API}/import/devices",
        files={"file": ("devices.csv", body, "text/csv")},
        data={"commit": "true", **data},
    ).json()


def test_fixing_a_hostname_updates_the_device_instead_of_adding_one(client, device_payload):
    """書き出して名前を直して戻したら、その機器が直ること。

    台帳を取り込んで作った DB にホスト名の誤りが大量にあることがある。
    ここで新しい機器が増える作りだと、直しようがない（誤った機器と
    正しい機器が両方並ぶだけになる）。
    """
    device_id = client.post(f"{API}/devices", json=device_payload(hostname="typo-sw-01")).json()[
        "id"
    ]

    result = _import(
        client,
        _csv(
            [
                "機器ID,ホスト名,ベンダー,型番",
                f"{device_id},core-sw-01,Cisco,C9500-40X",
            ]
        ),
    )

    assert result["created"] == 0
    assert result["updated"] == 1

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["core-sw-01"]
    assert devices[0]["id"] == device_id  # 同じ機器のまま


def test_the_exported_file_can_be_edited_and_taken_back(client, device_payload):
    """書き出し → 直す → 取り込み、の一周が通ること。"""
    client.post(f"{API}/devices", json=device_payload(hostname="wrong-01"))
    exported = client.get(f"{API}/export/devices.csv?include_status=false").text

    lines = exported.strip().split("\n")
    fixed = lines[0] + "\n" + lines[1].replace("wrong-01", "right-01")

    result = _import(client, fixed.encode("utf-8"))

    assert result["updated"] == 1
    assert result["created"] == 0
    assert [d["hostname"] for d in client.get(f"{API}/devices").json()] == ["right-01"]


def test_a_row_without_an_id_still_works_as_before(client):
    """機器IDを書かない行は今までどおり。新規に登録するときはこれ。"""
    result = _import(client, _csv(["ホスト名,ベンダー,型番", "new-sw-01,Cisco,C9300-48P"]))

    assert result["created"] == 1
    assert [d["hostname"] for d in client.get(f"{API}/devices").json()] == ["new-sw-01"]


def test_an_unknown_id_is_reported_instead_of_creating_a_device(client):
    """消された機器の行を黙って作り直さない。何が起きたか分かるようにする。"""
    result = _import(client, _csv(["機器ID,ホスト名,ベンダー,型番", "999,x-sw,Cisco,C9300-48P"]))

    assert result["errors"] == 1
    assert result["created"] == 0
    assert "台帳にありません" in result["rows"][0]["issues"][0]["message"]
    assert client.get(f"{API}/devices").json() == []


def test_two_devices_can_swap_hostnames_in_one_file(client, device_payload):
    """名前の入れ替えも通ること。ID で見ているから重複扱いにならない。"""
    a = client.post(f"{API}/devices", json=device_payload(hostname="sw-a")).json()["id"]
    b = client.post(
        f"{API}/devices", json=device_payload(hostname="sw-b", serial_number="X2")
    ).json()["id"]

    result = _import(
        client,
        _csv(
            [
                "機器ID,ホスト名,ベンダー,型番",
                f"{a},sw-b,Cisco,C9500-40X",
                f"{b},sw-a,Cisco,C9500-40X",
            ]
        ),
    )

    assert result["errors"] == 0, result["rows"]
    assert result["updated"] == 2
    names = {d["id"]: d["hostname"] for d in client.get(f"{API}/devices").json()}
    assert names[a] == "sw-b"
    assert names[b] == "sw-a"


def test_the_same_id_twice_in_one_file_is_reported(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload(hostname="dup-01")).json()["id"]

    result = _import(
        client,
        _csv(
            [
                "機器ID,ホスト名,ベンダー,型番",
                f"{device_id},name-a,Cisco,C9300-48P",
                f"{device_id},name-b,Cisco,C9300-48P",
            ]
        ),
    )

    assert result["errors"] == 1
    assert "重複" in result["rows"][1]["issues"][0]["message"]


def test_renaming_onto_another_device_is_reported_clearly(client, device_payload):
    """他の機器と同じ名前にしようとしたら、理由を示して止めること。

    ここで DB の重複エラーがそのまま出ると、何行目の何が悪いのか分からない。
    """
    a = client.post(f"{API}/devices", json=device_payload(hostname="keep-01")).json()["id"]
    client.post(f"{API}/devices", json=device_payload(hostname="taken-01", serial_number="X2"))

    result = _import(
        client,
        _csv(["機器ID,ホスト名,ベンダー,型番", f"{a},taken-01,Cisco,C9500-40X"]),
    )

    assert result["errors"] == 1
    assert result["created"] == 0
    message = result["rows"][0]["issues"][0]["message"]
    assert "taken-01" in message

    # 台帳は変わっていない
    names = sorted(d["hostname"] for d in client.get(f"{API}/devices").json())
    assert names == ["keep-01", "taken-01"]


def test_large_preview_returns_a_sample_not_every_row(client):
    """行結果を全部返すと、16万行では画面側が固まる。

    件数の内訳は全件から出し、中身はエラー優先で一部だけ返す。
    """
    from app.services import importer

    total = importer._ROW_SAMPLE + 80
    lines = ["ホスト名,ベンダー,型番"]
    for i in range(total):
        # 先頭付近にエラーを混ぜる。サンプルにエラーが残ることを見る
        if i in (3, 7):
            lines.append(f"bad-{i:04d},Cisco,")  # 型番欠落
        else:
            lines.append(f"sw-{i:04d},Cisco,C9300")

    body = upload(client, "\n".join(lines) + "\n").json()

    assert body["total_rows"] == total
    assert body["rows_total"] == total
    assert body["created"] == total - 2
    assert body["errors"] == 2
    assert len(body["rows"]) == importer._ROW_SAMPLE
    assert [r["action"] for r in body["rows"][:2]] == ["error", "error"]
    # プレビューなので台帳は空のまま
    assert client.get(f"{API}/devices").json() == []
