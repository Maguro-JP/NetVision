from tests.conftest import API


def test_create_and_get_device(client, device_payload):
    res = client.post(f"{API}/devices", json=device_payload())
    assert res.status_code == 201, res.text
    device_id = res.json()["id"]

    got = client.get(f"{API}/devices/{device_id}").json()
    assert got["hostname"] == "core-sw-01"
    assert got["os_version"] == "17.09.04a"
    assert got["lifecycle"]["overall_severity"] == "unknown"
    assert got["contracts"] == []


def test_duplicate_hostname_conflicts(client, device_payload):
    client.post(f"{API}/devices", json=device_payload())
    res = client.post(f"{API}/devices", json=device_payload(serial_number="X2"))
    assert res.status_code == 409


def test_eol_before_eos_is_rejected(client, device_payload):
    res = client.post(
        f"{API}/devices", json=device_payload(eos_date="2028-01-01", eol_date="2027-01-01")
    )
    assert res.status_code == 422


def test_patch_updates_only_given_fields(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    res = client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.12.03"})
    assert res.status_code == 200
    body = res.json()
    assert body["os_version"] == "17.12.03"
    assert body["model"] == "C9500-40X"


def test_patch_rejects_inconsistent_lifecycle_dates(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload(eos_date="2028-01-01")).json()[
        "id"
    ]
    res = client.patch(f"{API}/devices/{device_id}", json={"eol_date": "2027-01-01"})
    assert res.status_code == 422


def test_search_and_filter(client, device_payload):
    client.post(f"{API}/devices", json=device_payload())
    client.post(
        f"{API}/devices",
        json=device_payload(
            hostname="fw-01", vendor="Fortinet", model="FG-100F", device_type="firewall"
        ),
    )

    assert len(client.get(f"{API}/devices").json()) == 2
    assert len(client.get(f"{API}/devices", params={"vendor": "Fortinet"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"q": "C9500"}).json()) == 1
    assert len(client.get(f"{API}/devices", params={"device_type": "firewall"}).json()) == 1


def test_delete_device(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    assert client.delete(f"{API}/devices/{device_id}").status_code == 204
    assert client.get(f"{API}/devices/{device_id}").status_code == 404


def test_unknown_site_rejected(client, device_payload):
    res = client.post(f"{API}/devices", json=device_payload(site_id=999))
    assert res.status_code == 422
