"""拠点ID・管理IDの登録と、拠点台帳の更新。"""

from tests.conftest import API


def test_site_can_have_a_chosen_code(client):
    created = client.post(
        f"{API}/sites",
        json={"code": "TOK-DC1", "name": "東京DC", "address": "江東区", "notes": "本館"},
    ).json()
    assert created["code"] == "TOK-DC1"
    assert created["name"] == "東京DC"

    listed = client.get(f"{API}/sites").json()
    assert listed[0]["code"] == "TOK-DC1"


def test_site_code_must_be_unique(client):
    client.post(f"{API}/sites", json={"code": "OSK-1", "name": "大阪1"}).json()
    res = client.post(f"{API}/sites", json={"code": "OSK-1", "name": "大阪2"})
    assert res.status_code == 409


def test_site_can_be_edited_on_the_ledger(client):
    site_id = client.post(f"{API}/sites", json={"name": "仮拠点"}).json()["id"]
    updated = client.patch(
        f"{API}/sites/{site_id}",
        json={"code": "TMP-01", "name": "仮拠点A", "notes": "試験用"},
    ).json()
    assert updated["code"] == "TMP-01"
    assert updated["name"] == "仮拠点A"
    assert updated["notes"] == "試験用"


def test_blank_site_codes_do_not_collide(client):
    a = client.post(f"{API}/sites", json={"name": "拠点A"}).json()
    b = client.post(f"{API}/sites", json={"name": "拠点B", "code": "  "}).json()
    assert a["code"] is None
    assert b["code"] is None


def test_device_can_have_a_chosen_code(client, device_payload):
    created = client.post(
        f"{API}/devices", json=device_payload(code="ASSET-100", hostname="sw-a")
    ).json()
    assert created["code"] == "ASSET-100"

    clash = client.post(
        f"{API}/devices", json=device_payload(code="ASSET-100", hostname="sw-b", serial_number="X2")
    )
    assert clash.status_code == 409


def test_device_can_attach_site_by_code(client, device_payload):
    client.post(f"{API}/sites", json={"code": "NAG-1", "name": "名古屋"}).json()
    payload = device_payload(hostname="sw-nagoya")
    payload.pop("site_id", None)
    payload["site_code"] = "NAG-1"

    created = client.post(f"{API}/devices", json=payload).json()
    assert created["site_name"] == "名古屋"


def test_unknown_site_code_is_rejected(client, device_payload):
    payload = device_payload(hostname="sw-x")
    payload.pop("site_id", None)
    payload["site_code"] = "NO-SUCH"
    res = client.post(f"{API}/devices", json=payload)
    assert res.status_code == 422
