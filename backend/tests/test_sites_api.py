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
