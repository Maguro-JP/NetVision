import json
from datetime import date, timedelta

from tests.conftest import API

PRODUCT_CSV = (
    "ベンダー,型番,製品名,販売終了(EoS),サポート終了(EoL),推奨後継機,出典URL,情報取得日\n"
    "Cisco,C9500-40X,Catalyst 9500,2027/01/31,2030/01/31,C9500-48Y4C,https://cisco.com/eol,2026/08/01\n"
    "Juniper,MX204,MX204 Router,2025/10/09,2026/09/19,MX304,https://juniper.net/eol,2026/08/01\n"
)


def upload_products(client, body: str = PRODUCT_CSV, **data):
    return client.post(
        f"{API}/reference/products/import",
        files={"file": ("products.csv", body.encode("utf-8"), "text/csv")},
        data=data,
    )


def upload_vulnerabilities(client, payload, **data):
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        f"{API}/reference/vulnerabilities/import",
        files={"file": ("cve.json", body, "application/json")},
        data=data,
    )


def add_device(client, device_payload, **overrides):
    return client.post(f"{API}/devices", json=device_payload(**overrides)).json()["id"]


# ---------------------------------------------------------------------------
# 製品リファレンス
# ---------------------------------------------------------------------------


def test_product_csv_import(client):
    body = upload_products(client).json()

    assert body["created"] == 2
    assert body["errors"] == 0
    assert body["mapping"]["サポート終了(EoL)"] == "eol_date"

    products = client.get(f"{API}/reference/products").json()
    cisco = next(p for p in products if p["model"] == "C9500-40X")
    assert cisco["eol_date"] == "2030-01-31"
    assert cisco["recommended_replacement"] == "C9500-48Y4C"
    assert cisco["source"] == "csv"
    assert cisco["source_url"] == "https://cisco.com/eol"
    assert cisco["fetched_at"] == "2026-08-01"


def test_reimport_updates_instead_of_duplicating(client):
    upload_products(client)
    changed = PRODUCT_CSV.replace("2030/01/31", "2031/03/31")
    body = upload_products(client, changed).json()

    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 1

    products = client.get(f"{API}/reference/products").json()
    assert len(products) == 2
    cisco = next(p for p in products if p["model"] == "C9500-40X")
    assert cisco["eol_date"] == "2031-03-31"


def test_required_columns_are_reported(client):
    body = upload_products(client, "製品名,サポート終了(EoL)\nCatalyst,2030/01/31\n").json()
    assert body["missing_required_fields"] == ["vendor", "model"]
    assert body["rows"] == []


def test_bad_date_blocks_the_whole_import(client):
    broken = PRODUCT_CSV + "Fortinet,FG-100F,FortiGate,未定,,,,\n"
    body = upload_products(client, broken).json()

    assert body["errors"] == 1
    assert client.get(f"{API}/reference/products").json() == []


# ---------------------------------------------------------------------------
# 機器との紐付け（ここが正規化の肝）
# ---------------------------------------------------------------------------


def test_device_inherits_eol_from_product_reference(client, device_payload):
    """機器に EoL を入れなくても、型番の製品マスターから解決される。"""
    upload_products(client)
    device_id = add_device(client, device_payload, hostname="core-sw-01", model="C9500-40X")

    device = client.get(f"{API}/devices/{device_id}").json()
    lifecycle = device["lifecycle"]

    assert device["eol_date"] is None  # 機器自身には入っていない
    assert lifecycle["effective_eol_date"] == "2030-01-31"
    assert lifecycle["eol_source"] == "product"
    assert lifecycle["effective_eos_date"] == "2027-01-31"


def test_device_value_wins_over_product_reference(client, device_payload):
    """個体の事情（延長保守など）を機器側で上書きできる。"""
    upload_products(client)
    device_id = add_device(
        client, device_payload, model="C9500-40X", eol_date="2032-12-31", eos_date="2028-01-01"
    )

    lifecycle = client.get(f"{API}/devices/{device_id}").json()["lifecycle"]
    assert lifecycle["effective_eol_date"] == "2032-12-31"
    assert lifecycle["eol_source"] == "device"


def test_vendor_case_differences_still_match(client, device_payload):
    upload_products(client)
    device_id = add_device(client, device_payload, vendor="cisco", model="c9500-40x")

    lifecycle = client.get(f"{API}/devices/{device_id}").json()["lifecycle"]
    assert lifecycle["eol_source"] == "product"


def test_updating_one_reference_moves_every_device(client, device_payload):
    """型番に正規化したので、1回の更新で該当機器すべてに反映される。"""
    upload_products(client)
    for index in range(3):
        add_device(
            client,
            device_payload,
            hostname=f"sw-{index}",
            model="C9500-40X",
            serial_number=f"S{index}",
        )

    product = next(
        p for p in client.get(f"{API}/reference/products").json() if p["model"] == "C9500-40X"
    )
    assert product["device_count"] == 3

    client.patch(f"{API}/reference/products/{product['id']}", json={"eol_date": "2029-06-30"})

    devices = client.get(f"{API}/devices").json()
    assert {d["lifecycle"]["effective_eol_date"] for d in devices} == {"2029-06-30"}


def test_reference_drives_the_alert_severity(client, device_payload):
    """製品マスターの EoL でアラートが立つ。"""
    past = (date.today() - timedelta(days=30)).strftime("%Y/%m/%d")
    upload_products(client, f"ベンダー,型番,サポート終了(EoL)\nCisco,C2960X,{past}\n")
    add_device(client, device_payload, model="C2960X")

    device = client.get(f"{API}/devices").json()[0]
    assert device["lifecycle"]["overall_severity"] == "expired"


def test_product_can_be_created_and_deleted_by_hand(client):
    res = client.post(
        f"{API}/reference/products",
        json={"vendor": "Fortinet", "model": "FG-100F", "eol_date": "2029-01-31"},
    )
    assert res.status_code == 201
    product_id = res.json()["id"]
    assert res.json()["source"] == "manual"

    assert (
        client.post(
            f"{API}/reference/products", json={"vendor": "fortinet", "model": "fg-100f"}
        ).status_code
        == 409
    )

    assert client.delete(f"{API}/reference/products/{product_id}").status_code == 204


# ---------------------------------------------------------------------------
# 鮮度
# ---------------------------------------------------------------------------


def test_freshness_flags_old_and_missing_references(client, device_payload):
    old = (date.today() - timedelta(days=400)).strftime("%Y/%m/%d")
    upload_products(
        client, f"ベンダー,型番,サポート終了(EoL),情報取得日\nCisco,C9500-40X,2030/01/31,{old}\n"
    )
    add_device(client, device_payload, model="C9500-40X")
    add_device(
        client,
        device_payload,
        hostname="fw-01",
        vendor="Fortinet",
        model="FG-100F",
        serial_number="X2",
    )

    body = client.get(f"{API}/reference/freshness").json()

    assert body["product_count"] == 1
    assert body["products_stale"] == 1  # 180日より古い
    assert body["devices_total"] == 2
    assert body["devices_with_product_reference"] == 1
    assert body["devices_without_reference"] == ["Fortinet FG-100F"]


def test_recent_reference_is_not_stale(client):
    recent = date.today().strftime("%Y/%m/%d")
    upload_products(
        client, f"ベンダー,型番,サポート終了(EoL),情報取得日\nCisco,C9500-40X,2030/01/31,{recent}\n"
    )

    body = client.get(f"{API}/reference/freshness").json()
    assert body["products_stale"] == 0

    product = client.get(f"{API}/reference/products").json()[0]
    assert product["stale"] is False
    assert product["age_days"] == 0


def test_stale_only_filter(client):
    old = (date.today() - timedelta(days=400)).strftime("%Y/%m/%d")
    recent = date.today().strftime("%Y/%m/%d")
    upload_products(
        client,
        f"ベンダー,型番,情報取得日\nCisco,C9500-40X,{old}\nJuniper,MX204,{recent}\n",
    )

    stale = client.get(f"{API}/reference/products", params={"stale_only": "true"}).json()
    assert [p["model"] for p in stale] == ["C9500-40X"]


def test_template_is_downloadable(client):
    res = client.get(f"{API}/reference/products/template.csv")
    assert res.status_code == 200
    assert res.text.startswith("﻿")
    assert "サポート終了(EoL)" in res.text
    assert "情報取得日" in res.text
