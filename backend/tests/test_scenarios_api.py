from datetime import date, timedelta

from tests.conftest import API


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def make_scenario(client, **overrides):
    payload = {"name": "2027年度 更改案", "description": "コアスイッチの更改"}
    payload.update(overrides)
    return client.post(f"{API}/scenarios", json=payload).json()


def add_device(client, device_payload, **overrides):
    return client.post(f"{API}/devices", json=device_payload(**overrides)).json()["id"]


def add_contract(client, device_id, **overrides):
    payload = {
        "provider": "ネットワン",
        "start_date": _in(-365),
        "end_date": _in(365),
        "annual_cost": 480000,
    }
    payload.update(overrides)
    return client.post(f"{API}/devices/{device_id}/contracts", json=payload).json()


def summary(client, scenario_id):
    return client.get(f"{API}/scenarios/{scenario_id}/summary").json()


def test_scenario_crud(client):
    scenario = make_scenario(client)
    assert scenario["status"] == "draft"
    assert scenario["items"] == []

    scenario_id = scenario["id"]
    updated = client.patch(
        f"{API}/scenarios/{scenario_id}", json={"status": "review", "target_date": "2027-04-01"}
    ).json()
    assert updated["status"] == "review"
    assert updated["target_date"] == "2027-04-01"

    assert len(client.get(f"{API}/scenarios").json()) == 1
    assert client.delete(f"{API}/scenarios/{scenario_id}").status_code == 204
    assert client.get(f"{API}/scenarios/{scenario_id}").status_code == 404


def test_duplicate_name_conflicts(client):
    make_scenario(client)
    assert client.post(f"{API}/scenarios", json={"name": "2027年度 更改案"}).status_code == 409


def test_scenario_does_not_touch_the_real_inventory(client, device_payload):
    device_id = add_device(client, device_payload)
    scenario_id = make_scenario(client)["id"]

    client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={
            "action": "replace",
            "device_id": device_id,
            "planned_vendor": "Cisco",
            "planned_model": "C9500-48Y4C",
            "unit_cost": 3000000,
        },
    )

    # 台帳側は一切変わらない
    device = client.get(f"{API}/devices/{device_id}").json()
    assert device["model"] == "C9500-40X"
    assert len(client.get(f"{API}/devices").json()) == 1


def test_replace_swaps_the_device_in_the_projection(client, device_payload):
    device_id = add_device(client, device_payload, eol_date=_in(-10))
    add_contract(client, device_id, annual_cost=480000)
    scenario_id = make_scenario(client)["id"]

    client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={
            "action": "replace",
            "device_id": device_id,
            "planned_model": "C9500-48Y4C",
            "unit_cost": 3000000,
            "planned_annual_cost": 300000,
            "planned_install_date": _in(180),
            "planned_eol_date": _in(180 + 365 * 8),
        },
    )

    body = summary(client, scenario_id)
    assert body["current"]["device_count"] == 1
    assert body["current"]["unsupported_count"] == 1
    assert body["current"]["annual_maintenance_cost"] == 480000

    assert body["projected"]["device_count"] == 1
    assert body["projected"]["unsupported_count"] == 0  # EoL 切れが解消する
    assert body["projected"]["annual_maintenance_cost"] == 300000

    assert body["investment"] == 3000000
    assert body["annual_cost_delta"] == -180000  # 年額18万円の削減
    assert body["item_counts"] == {"replace": 1}


def test_add_and_remove_change_the_device_count(client, device_payload):
    keep_id = add_device(client, device_payload, hostname="sw-keep")
    drop_id = add_device(client, device_payload, hostname="sw-drop", serial_number="X2")
    add_contract(client, drop_id, annual_cost=200000)
    scenario_id = make_scenario(client)["id"]

    client.post(
        f"{API}/scenarios/{scenario_id}/items", json={"action": "remove", "device_id": drop_id}
    )
    client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={
            "action": "add",
            "planned_model": "C9300-48P",
            "quantity": 3,
            "unit_cost": 800000,
            "planned_annual_cost": 100000,
        },
    )

    body = summary(client, scenario_id)
    assert body["current"]["device_count"] == 2
    assert body["projected"]["device_count"] == 4  # 2 - 1 + 3
    assert body["investment"] == 2400000
    assert body["annual_cost_delta"] == 100000  # -200000 + 300000
    assert keep_id  # 残す機器は指定なしでもそのまま残る


def test_quantity_is_multiplied_for_added_devices(client):
    scenario_id = make_scenario(client)["id"]
    client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={
            "action": "add",
            "planned_model": "AP-515",
            "quantity": 50,
            "unit_cost": 60000,
            "planned_annual_cost": 5000,
        },
    )

    body = summary(client, scenario_id)
    assert body["projected"]["device_count"] == 50
    assert body["investment"] == 3000000
    assert body["annual_cost_delta"] == 250000


def test_generate_creates_a_draft_from_expiring_devices(client, device_payload):
    add_device(client, device_payload, hostname="sw-soon", eol_date=_in(200))
    add_device(client, device_payload, hostname="sw-later", serial_number="X2", eol_date=_in(2000))
    add_device(client, device_payload, hostname="sw-unknown", serial_number="X3")

    scenario_id = make_scenario(client)["id"]
    body = client.post(f"{API}/scenarios/{scenario_id}/generate?within_days=730").json()

    # 期限が近い機器だけが、現行構成を引き継いだ入れ替え案として並ぶ
    assert [i["device_label"] for i in body["items"]] == ["sw-soon"]
    item = body["items"][0]
    assert item["action"] == "replace"
    assert item["planned_model"] == "C9500-40X"
    assert item["planned_vendor"] == "Cisco"
    assert "残り 200 日" in item["notes"]


def test_generate_is_idempotent_for_the_same_device(client, device_payload):
    add_device(client, device_payload, eol_date=_in(200))
    scenario_id = make_scenario(client)["id"]

    client.post(f"{API}/scenarios/{scenario_id}/generate")
    body = client.post(f"{API}/scenarios/{scenario_id}/generate").json()

    assert len(body["items"]) == 1


def test_generate_skips_retired_devices(client, device_payload):
    add_device(client, device_payload, status="retired", eol_date=_in(-100))
    scenario_id = make_scenario(client)["id"]

    assert client.post(f"{API}/scenarios/{scenario_id}/generate").json()["items"] == []


def test_items_can_be_edited_and_deleted(client, device_payload):
    device_id = add_device(client, device_payload)
    scenario_id = make_scenario(client)["id"]
    item = client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={"action": "replace", "device_id": device_id, "planned_model": "旧案"},
    ).json()

    updated = client.patch(
        f"{API}/scenarios/items/{item['id']}", json={"planned_model": "新案", "quantity": 2}
    ).json()
    assert updated["planned_model"] == "新案"
    assert updated["quantity"] == 2

    assert client.delete(f"{API}/scenarios/items/{item['id']}").status_code == 204
    assert client.get(f"{API}/scenarios/{scenario_id}").json()["items"] == []


def test_replace_requires_a_target_device(client):
    scenario_id = make_scenario(client)["id"]
    res = client.post(
        f"{API}/scenarios/{scenario_id}/items", json={"action": "replace", "planned_model": "X"}
    )
    assert res.status_code == 422


def test_unknown_device_is_rejected(client):
    scenario_id = make_scenario(client)["id"]
    res = client.post(
        f"{API}/scenarios/{scenario_id}/items", json={"action": "remove", "device_id": 999}
    )
    assert res.status_code == 422


def test_deleting_a_device_keeps_the_plan_but_warns(client, device_payload):
    device_id = add_device(client, device_payload)
    scenario_id = make_scenario(client)["id"]
    client.post(
        f"{API}/scenarios/{scenario_id}/items",
        json={"action": "replace", "device_id": device_id, "planned_model": "後継機"},
    )

    client.delete(f"{API}/devices/{device_id}")

    detail = client.get(f"{API}/scenarios/{scenario_id}").json()
    assert detail["items"][0]["device_label"] == "core-sw-01"  # 何の案だったかは残る
    assert detail["items"][0]["device_id"] is None

    body = summary(client, scenario_id)
    assert any("台帳に見つからない" in w for w in body["warnings"])


def test_missing_planned_model_is_warned(client, device_payload):
    device_id = add_device(client, device_payload)
    scenario_id = make_scenario(client)["id"]
    client.post(
        f"{API}/scenarios/{scenario_id}/items", json={"action": "replace", "device_id": device_id}
    )

    assert any("型番が未入力" in w for w in summary(client, scenario_id)["warnings"])


def test_target_date_moves_the_evaluation_point(client, device_payload):
    add_device(client, device_payload, eol_date=_in(100))

    now_id = make_scenario(client, name="現時点")["id"]
    future_id = make_scenario(client, name="将来", target_date=_in(200))["id"]

    assert summary(client, now_id)["current"]["unsupported_count"] == 0
    # 200日後の視点では、その機器はサポート切れになっている
    assert summary(client, future_id)["current"]["unsupported_count"] == 1


def test_retired_devices_are_excluded_from_both_sides(client, device_payload):
    add_device(client, device_payload, status="retired")
    scenario_id = make_scenario(client)["id"]

    body = summary(client, scenario_id)
    assert body["current"]["device_count"] == 0
    assert body["projected"]["device_count"] == 0
