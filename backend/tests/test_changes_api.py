from tests.conftest import API

CSV = (
    "ホスト名,メーカー,型番,設置場所,保守ベンダー,保守開始日,保守終了日\n"
    "core-sw-01,Cisco,C9500-40X,本社DC,ネットワン,2024/4/1,2027/3/31\n"
)


def changes(client, **params):
    return client.get(f"{API}/changes", params=params).json()


def test_device_creation_is_recorded(client, device_payload):
    client.post(f"{API}/devices", json=device_payload())

    body = changes(client, entity_type="device")
    assert body["total"] == 1

    log = body["items"][0]
    assert log["action"] == "create"
    assert log["entity_label"] == "core-sw-01"
    assert log["source"] == "ui"
    assert "C9500-40X" in log["new_value"]


def test_updates_are_recorded_per_field(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    client.patch(
        f"{API}/devices/{device_id}",
        json={"os_version": "17.12.03", "rack": "A-01"},
    )

    updates = changes(client, entity_type="device", action="update")["items"]
    fields = {u["field"]: u for u in updates}

    assert set(fields) == {"os_version", "rack"}
    assert fields["os_version"]["old_value"] == "17.09.04a"
    assert fields["os_version"]["new_value"] == "17.12.03"
    assert fields["os_version"]["field_label"] == "OSバージョン"
    assert fields["rack"]["old_value"] is None
    assert fields["rack"]["new_value"] == "A-01"
    assert fields["rack"]["entity_id"] == device_id


def test_unchanged_patch_records_nothing(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.09.04a"})

    assert changes(client, action="update")["total"] == 0


def test_deletion_is_recorded_and_survives_the_device(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    client.delete(f"{API}/devices/{device_id}")

    deleted = changes(client, action="delete")["items"]
    assert len(deleted) == 1
    # 機器が消えた後も、何を消したかが分かる
    assert deleted[0]["entity_label"] == "core-sw-01"
    assert "C9500-40X" in deleted[0]["old_value"]


def test_import_changes_are_grouped_into_one_batch(client):
    client.post(
        f"{API}/import/devices",
        files={"file": ("daichou.csv", CSV.encode("utf-8"), "text/csv")},
    )

    body = changes(client, source="import")
    assert body["total"] >= 3  # 機器 + 拠点 + 保守契約

    batches = {item["batch_id"] for item in body["items"]}
    assert len(batches) == 1  # 取り込み1回はまとめて1バッチ
    assert all(item["note"] == "台帳の取り込み" for item in body["items"])

    types = {item["entity_type"] for item in body["items"]}
    assert types == {"device", "site", "contract"}


def test_import_update_records_only_changed_fields(client):
    client.post(f"{API}/import/devices", files={"file": ("a.csv", CSV.encode("utf-8"), "text/csv")})
    updated = CSV.replace("C9500-40X", "C9500-48Y")
    client.post(
        f"{API}/import/devices", files={"file": ("b.csv", updated.encode("utf-8"), "text/csv")}
    )

    updates = changes(client, action="update", entity_type="device")["items"]
    assert [(u["field"], u["old_value"], u["new_value"]) for u in updates] == [
        ("model", "C9500-40X", "C9500-48Y")
    ]


def test_preview_does_not_record_history(client):
    client.post(
        f"{API}/import/devices/preview",
        files={"file": ("daichou.csv", CSV.encode("utf-8"), "text/csv")},
    )
    assert changes(client)["total"] == 0


def test_contract_changes_are_recorded_against_the_device(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    contract_id = client.post(
        f"{API}/devices/{device_id}/contracts",
        json={"provider": "ネットワン", "start_date": "2024-04-01", "end_date": "2027-03-31"},
    ).json()["id"]
    client.patch(f"{API}/contracts/{contract_id}", json={"end_date": "2028-03-31"})

    logs = changes(client, entity_type="contract")["items"]
    assert {log["action"] for log in logs} == {"create", "update"}

    update = next(log for log in logs if log["action"] == "update")
    assert update["field"] == "end_date"
    assert update["old_value"] == "2027-03-31"
    assert update["new_value"] == "2028-03-31"
    assert "core-sw-01" in update["entity_label"]


def test_history_is_filterable(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    client.post(f"{API}/devices", json=device_payload(hostname="fw-01", vendor="Fortinet"))
    client.patch(f"{API}/devices/{device_id}", json={"notes": "更改検討"})

    assert changes(client, entity_id=device_id)["total"] == 2
    assert changes(client, q="fw-01")["total"] == 1
    assert changes(client, q="更改検討")["total"] == 1
    assert changes(client, action="create")["total"] == 2


def test_history_is_newest_first_and_paginated(client, device_payload):
    for index in range(5):
        client.post(f"{API}/devices", json=device_payload(hostname=f"sw-{index:02d}"))

    body = changes(client, limit=2)
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["items"][0]["entity_label"] == "sw-04"

    page2 = changes(client, limit=2, offset=2)
    assert page2["items"][0]["entity_label"] == "sw-02"


def test_site_changes_are_recorded(client):
    site_id = client.post(f"{API}/sites", json={"name": "本社DC"}).json()["id"]
    client.patch(f"{API}/sites/{site_id}", json={"address": "東京都千代田区"})

    logs = changes(client, entity_type="site")["items"]
    assert {log["action"] for log in logs} == {"create", "update"}
    update = next(log for log in logs if log["action"] == "update")
    assert update["field"] == "address"
    assert update["new_value"] == "東京都千代田区"
