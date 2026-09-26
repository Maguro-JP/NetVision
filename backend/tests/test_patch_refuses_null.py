"""更新（PATCH）で、空を許さない項目に null を送ると 422 で断る。

以前は null をそのまま DB に書こうとして NOT NULL 違反の 500 になっていた。
省略した項目は今までどおり変えない。
"""

import pytest

from tests.conftest import API


@pytest.fixture()
def ids(client):
    site = client.post(f"{API}/sites", json={"code": "HQ", "name": "本社"}).json()
    device = client.post(
        f"{API}/devices",
        json={"hostname": "sw1", "vendor": "Cisco", "model": "C9300-48P", "site_id": site["id"]},
    ).json()
    scenario = client.post(f"{API}/scenarios", json={"name": "更改"}).json()
    return {"sites": site["id"], "devices": device["id"], "scenarios": scenario["id"]}


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("devices", "hostname"),
        ("devices", "vendor"),
        ("devices", "model"),
        ("devices", "device_type"),
        ("devices", "status"),
        ("sites", "name"),
        ("sites", "status"),
        ("scenarios", "name"),
        ("scenarios", "status"),
    ],
)
def test_null_for_a_required_field_is_refused(client, ids, kind, field) -> None:
    response = client.patch(f"{API}/{kind}/{ids[kind]}", json={field: None})
    assert response.status_code == 422


def test_leaving_a_field_out_still_keeps_it(client, ids) -> None:
    response = client.patch(f"{API}/devices/{ids['devices']}", json={"notes": "更新"})
    assert response.status_code == 200
    body = response.json()
    assert body["hostname"] == "sw1"
    assert body["notes"] == "更新"


def test_optional_fields_can_still_be_cleared(client, ids) -> None:
    """空を許す項目は、今までどおり null で消せる。"""
    client.patch(f"{API}/devices/{ids['devices']}", json={"notes": "消す"})
    response = client.patch(f"{API}/devices/{ids['devices']}", json={"notes": None})
    assert response.status_code == 200
    assert response.json()["notes"] is None


def test_null_for_a_required_scenario_item_field_is_refused(client, ids):
    item = client.post(
        f"{API}/scenarios/{ids['scenarios']}/items",
        json={"action": "replace", "device_id": ids["devices"]},
    ).json()
    for field in ("action", "quantity"):
        response = client.patch(f"{API}/scenarios/items/{item['id']}", json={field: None})
        assert response.status_code == 422


def test_null_for_a_required_contract_field_is_refused(client, ids):
    contract = client.post(
        f"{API}/devices/{ids['devices']}/contracts",
        json={"provider": "保守会社", "start_date": "2026-01-01", "end_date": "2027-01-01"},
    ).json()
    for field in ("provider", "start_date", "end_date"):
        response = client.patch(f"{API}/contracts/{contract['id']}", json={field: None})
        assert response.status_code == 422
