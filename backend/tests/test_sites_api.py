"""拠点の台帳。

拠点は名前と住所だけでは足りない。移転で住所だけ変わることもあれば、
名前はそのままで一時閉鎖・閉店になることもある。機器を消さずに
「この拠点はいま使っていない」と言えることを確かめる。
"""

API = "/api/v1"


def test_a_site_keeps_its_devices_when_it_closes(client):
    """閉店しても機器は残す。引き上げの棚卸しに要る。"""
    site = client.post(f"{API}/sites", json={"name": "青森県営業所"}).json()
    client.post(
        f"{API}/devices",
        json={"hostname": "aomori-rt-01", "site_id": site["id"]},
    )

    updated = client.patch(
        f"{API}/sites/{site['id']}",
        json={"status": "closed", "closed_on": "2026-06-30", "notes": "機器の引き上げ待ち"},
    ).json()
    assert updated["status"] == "closed"
    assert updated["closed_on"] == "2026-06-30"
    assert updated["device_count"] == 1

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["aomori-rt-01"]


def test_moving_a_site_only_changes_the_address(client):
    """移転は住所を書き換えるだけ。拠点も機器も作り直さない。"""
    site = client.post(
        f"{API}/sites", json={"name": "千葉県営業所", "address": "千葉県千葉市美浜区"}
    ).json()
    moved = client.patch(f"{API}/sites/{site['id']}", json={"address": "千葉県千葉市中央区"}).json()
    assert moved["id"] == site["id"]
    assert moved["name"] == "千葉県営業所"
    assert moved["address"] == "千葉県千葉市中央区"


def test_a_new_site_is_active(client):
    """既定は稼働中。何も選ばずに登録できる。"""
    site = client.post(f"{API}/sites", json={"name": "新しい営業所"}).json()
    assert site["status"] == "active"
    assert site["opened_on"] is None
    assert site["closed_on"] is None


def test_sites_can_be_exported(client):
    """拠点の台帳を書き出せること。閉鎖した拠点に機器が残っていないかを、
    表の上で数えられるよう台数も入れる。"""
    site = client.post(
        f"{API}/sites",
        json={"name": "青森県営業所", "code": "AOM-1", "status": "closed", "address": "青森県"},
    ).json()
    client.post(f"{API}/devices", json={"hostname": "aomori-rt-01", "site_id": site["id"]})

    res = client.get(f"{API}/export/sites.csv")
    assert res.status_code == 200
    lines = res.text.lstrip("﻿").strip().splitlines()
    assert lines[0].split(",") == [
        "拠点ID",
        "拠点名",
        "状態",
        "住所",
        "開設日",
        "閉鎖日",
        "機器数",
        "メモ",
    ]
    row = dict(zip(lines[0].split(","), lines[1].split(","), strict=False))
    assert row["拠点ID"] == "AOM-1"
    assert row["状態"] == "閉鎖・閉店"
    assert row["機器数"] == "1"

    book = client.get(f"{API}/export/sites.xlsx")
    assert book.status_code == 200
    assert "spreadsheetml" in book.headers["content-type"]


def test_two_sites_can_share_a_name_when_they_have_ids(client):
    """同名のビル・同名の営業所は普通にある。拠点IDで区別できるなら通す。"""
    first = client.post(f"{API}/sites", json={"name": "大手町ビル", "code": "S-001"})
    second = client.post(f"{API}/sites", json={"name": "大手町ビル", "code": "S-002"})
    assert first.status_code == 201
    assert second.status_code == 201


def test_a_site_without_an_id_still_needs_a_free_name(client):
    """拠点IDが無いものどうしは名前でしか見分けられないので、そこは止める。"""
    client.post(f"{API}/sites", json={"name": "青森県営業所"})
    again = client.post(f"{API}/sites", json={"name": "青森県営業所"})
    assert again.status_code == 409
    assert "拠点ID" in again.json()["detail"]
