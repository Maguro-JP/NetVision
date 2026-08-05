from datetime import date, timedelta

from tests.conftest import API


def _contract(**overrides):
    payload = {
        "provider": "ネットワン",
        "contract_number": "MC-2024-001",
        "service_level": "24x365 4時間駆付",
        "start_date": "2024-04-01",
        "end_date": "2027-03-31",
        "annual_cost": 480000,
    }
    payload.update(overrides)
    return payload


def test_contract_lifecycle(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]

    res = client.post(f"{API}/devices/{device_id}/contracts", json=_contract())
    assert res.status_code == 201, res.text
    contract_id = res.json()["id"]

    detail = client.get(f"{API}/devices/{device_id}").json()
    assert len(detail["contracts"]) == 1
    assert detail["lifecycle"]["maintenance_end"] == "2027-03-31"

    res = client.patch(f"{API}/contracts/{contract_id}", json={"end_date": "2028-03-31"})
    assert res.status_code == 200
    assert (
        client.get(f"{API}/devices/{device_id}").json()["lifecycle"]["maintenance_end"]
        == "2028-03-31"
    )

    assert client.delete(f"{API}/contracts/{contract_id}").status_code == 204
    assert client.get(f"{API}/devices/{device_id}").json()["lifecycle"]["maintenance_end"] is None


def test_end_before_start_rejected(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    res = client.post(
        f"{API}/devices/{device_id}/contracts",
        json=_contract(start_date="2027-04-01", end_date="2024-03-31"),
    )
    assert res.status_code == 422


def test_contract_on_missing_device(client):
    assert client.post(f"{API}/devices/999/contracts", json=_contract()).status_code == 404


def test_expiring_contract_drives_alert(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    soon = date.today() + timedelta(days=30)
    client.post(
        f"{API}/devices/{device_id}/contracts",
        json=_contract(start_date="2024-04-01", end_date=soon.isoformat()),
    )

    lifecycle = client.get(f"{API}/devices/{device_id}").json()["lifecycle"]
    assert lifecycle["maintenance_severity"] == "critical"
    assert lifecycle["days_to_maintenance_end"] == 30

    alerts = client.get(f"{API}/alerts", params={"within_days": 60}).json()
    assert [a["id"] for a in alerts] == [device_id]
