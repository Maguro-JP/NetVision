from datetime import date, timedelta

from tests.conftest import API


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def add_device(client, payload_factory, **overrides):
    return client.post(f"{API}/devices", json=payload_factory(**overrides)).json()["id"]


def add_contract(client, device_id, **overrides):
    payload = {
        "provider": "ネットワン",
        "start_date": _in(-365),
        "end_date": _in(365),
        "annual_cost": 480000,
    }
    payload.update(overrides)
    return client.post(f"{API}/devices/{device_id}/contracts", json=payload)


def insights(client, **params):
    return client.get(f"{API}/insights", params=params).json()


def kinds(body) -> set[str]:
    return {i["kind"] for i in body["insights"]}


def test_retired_device_with_live_contract_is_flagged_with_saving(client, device_payload):
    device_id = add_device(client, device_payload, status="retired")
    add_contract(client, device_id)

    body = insights(client, category="cost")
    flagged = next(i for i in body["insights"] if i["kind"] == "contract_on_retired")

    assert flagged["device_hostname"] == "core-sw-01"
    assert flagged["annual_saving"] == 480000
    assert body["annual_saving_total"] == 480000


def test_standby_device_contract_counts_half_as_saving(client, device_payload):
    device_id = add_device(client, device_payload, status="standby")
    add_contract(client, device_id, annual_cost=300000)

    flagged = next(i for i in insights(client)["insights"] if i["kind"] == "contract_on_standby")
    assert flagged["annual_saving"] == 150000


def test_overlapping_contracts_are_flagged(client, device_payload):
    device_id = add_device(client, device_payload)
    add_contract(client, device_id, provider="A社", annual_cost=400000)
    add_contract(client, device_id, provider="B社", annual_cost=300000)

    flagged = next(i for i in insights(client)["insights"] if i["kind"] == "duplicate_contract")
    assert flagged["annual_saving"] == 300000  # 安い方を削減余地とみなす
    assert "A社" in flagged["detail"] and "B社" in flagged["detail"]


def test_sequential_contracts_are_not_flagged_as_duplicates(client, device_payload):
    device_id = add_device(client, device_payload)
    add_contract(client, device_id, start_date=_in(-700), end_date=_in(-1))
    add_contract(client, device_id, start_date=_in(0), end_date=_in(700))

    assert "duplicate_contract" not in kinds(insights(client))


def test_active_device_past_eol_is_a_risk(client, device_payload):
    add_device(client, device_payload, eol_date=_in(-30))

    flagged = next(i for i in insights(client)["insights"] if i["kind"] == "active_after_eol")
    assert flagged["severity"] == "expired"
    assert "30 日経過" in flagged["detail"]


def test_retired_device_past_eol_is_not_a_risk(client, device_payload):
    add_device(client, device_payload, status="retired", eol_date=_in(-30))
    assert "active_after_eol" not in kinds(insights(client))


def test_active_device_without_contract_is_a_risk(client, device_payload):
    add_device(client, device_payload, eol_date=_in(900))
    assert "active_without_contract" in kinds(insights(client))


def test_expired_contract_still_counts_as_no_contract(client, device_payload):
    device_id = add_device(client, device_payload, eol_date=_in(900))
    add_contract(client, device_id, start_date=_in(-700), end_date=_in(-10))

    flagged = next(
        i for i in insights(client)["insights"] if i["kind"] == "active_without_contract"
    )
    assert flagged["detail"] == "有効な保守契約がありません"


def test_aging_device_is_flagged(client, device_payload):
    add_device(client, device_payload, install_date=_in(-365 * 8))
    assert "aging_device" in kinds(insights(client))


def test_recently_installed_device_is_not_aging(client, device_payload):
    add_device(client, device_payload, install_date=_in(-365 * 2))
    assert "aging_device" not in kinds(insights(client))


def test_missing_data_is_reported(client, device_payload):
    add_device(client, device_payload)

    body = insights(client, category="data")
    assert kinds(body) == {"missing_lifecycle_data", "missing_site"}
    assert body["annual_saving_total"] == 0


def test_contract_beyond_eol_is_flagged(client, device_payload):
    device_id = add_device(client, device_payload, eol_date=_in(30))
    add_contract(client, device_id, end_date=_in(400))

    flagged = next(i for i in insights(client)["insights"] if i["kind"] == "contract_after_eol")
    assert "370 日先" in flagged["detail"]


def test_mixed_providers_for_same_model_are_flagged(client, device_payload):
    first = add_device(client, device_payload, hostname="sw-01")
    second = add_device(client, device_payload, hostname="sw-02", serial_number="X2")
    add_contract(client, first, provider="A社")
    add_contract(client, second, provider="B社")

    flagged = next(i for i in insights(client)["insights"] if i["kind"] == "mixed_providers")
    assert "C9500-40X" in flagged["detail"]
    assert "A社: 1台" in flagged["detail"]


def test_same_provider_is_not_flagged(client, device_payload):
    first = add_device(client, device_payload, hostname="sw-01")
    second = add_device(client, device_payload, hostname="sw-02", serial_number="X2")
    add_contract(client, first, provider="A社")
    add_contract(client, second, provider="A社")

    assert "mixed_providers" not in kinds(insights(client))


def test_insights_are_sorted_by_severity(client, device_payload):
    add_device(client, device_payload, hostname="sw-old", eol_date=_in(-10))
    add_device(client, device_payload, hostname="sw-new", serial_number="X2", eol_date=_in(2000))

    severities = [i["severity"] for i in insights(client)["insights"]]
    order = ["expired", "critical", "warning", "unknown", "ok"]
    assert severities == sorted(severities, key=order.index)


def test_filter_by_site(client, device_payload):
    site_id = client.post(f"{API}/sites", json={"name": "本社DC"}).json()["id"]
    add_device(client, device_payload, hostname="sw-hq", site_id=site_id, eol_date=_in(-10))
    add_device(client, device_payload, hostname="sw-other", serial_number="X2", eol_date=_in(-10))

    hosts = {i["device_hostname"] for i in insights(client, site_id=site_id)["insights"]}
    assert hosts == {"sw-hq"}


def test_empty_database_has_no_insights(client):
    body = insights(client)
    assert body["total"] == 0
    assert body["insights"] == []
