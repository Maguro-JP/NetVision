from pathlib import Path

from tests.conftest import API

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / f"{name}.txt").read_bytes()


def upload(client, *names, apply_inventory=False, device_id=None, encoding=None):
    files = [
        ("files", (f"{name}.txt", _encode(load(name), encoding), "text/plain")) for name in names
    ]
    data = {"apply_inventory": str(apply_inventory).lower()}
    if device_id is not None:
        data["device_id"] = str(device_id)
    return client.post(f"{API}/configs/upload", files=files, data=data)


def _encode(content: bytes, encoding: str | None) -> bytes:
    return content.decode("utf-8").encode(encoding) if encoding else content


def add_device(client, device_payload, **overrides):
    return client.post(f"{API}/devices", json=device_payload(**overrides)).json()["id"]


def seed_fleet(client, device_payload):
    """fixture の Config に対応する機器を台帳に用意する。"""
    return {
        "core": add_device(client, device_payload, hostname="core-sw-01"),
        "edge": add_device(
            client,
            device_payload,
            hostname="edge-rt-01",
            vendor="Juniper",
            model="MX204",
            serial_number="X2",
            device_type="router",
        ),
        "acc": add_device(
            client, device_payload, hostname="acc-sw-osaka-01", model="C2960X", serial_number="X3"
        ),
    }


def test_upload_extracts_identity_and_interfaces(client, device_payload):
    ids = seed_fleet(client, device_payload)

    body = upload(client, "core-sw-01").json()[0]
    assert body["vendor"] == "cisco_ios"
    assert body["vendor_label"] == "Cisco IOS / IOS-XE"
    assert body["hostname"] == "core-sw-01"
    assert body["matched"] is True
    assert body["device_id"] == ids["core"]
    assert body["interface_count"] == 4
    assert body["neighbor_count"] == 2
    assert body["vlan_count"] == 2

    interfaces = client.get(f"{API}/configs/devices/{ids['core']}/interfaces").json()
    names = {i["name"]: i for i in interfaces}
    assert names["Vlan10"]["ip_address"] == "10.0.0.11/24"
    assert names["GigabitEthernet1/0/24"]["enabled"] is False


def test_inventory_is_not_overwritten_without_approval(client, device_payload):
    ids = seed_fleet(client, device_payload)

    body = upload(client, "core-sw-01").json()[0]
    diffs = {d["field"]: d for d in body["diffs"]}

    # 台帳のシリアルは未登録、Config には入っている → 差分として提示される
    assert diffs["serial_number"]["observed"] == "FOC2401A001"
    assert diffs["serial_number"]["differs"] is True
    assert body["applied_fields"] == []

    device = client.get(f"{API}/devices/{ids['core']}").json()
    assert device["serial_number"] is None  # 台帳は変わっていない


def test_inventory_can_be_applied_explicitly(client, device_payload):
    ids = seed_fleet(client, device_payload)

    body = upload(client, "core-sw-01", apply_inventory=True).json()[0]
    assert "シリアル番号" in body["applied_fields"]

    device = client.get(f"{API}/devices/{ids['core']}").json()
    assert device["serial_number"] == "FOC2401A001"
    assert device["os_version"] == "17.09.04a"
    assert device["os_name"] == "IOS-XE"


def test_applying_config_is_recorded_in_history(client, device_payload):
    seed_fleet(client, device_payload)
    upload(client, "core-sw-01", apply_inventory=True)

    logs = client.get(f"{API}/changes", params={"source": "system"}).json()["items"]
    fields = {log["field"] for log in logs if log["action"] == "update"}
    assert "serial_number" in fields
    assert all("Config取り込み" in log["note"] for log in logs)


def test_unmatched_hostname_is_still_stored(client):
    body = upload(client, "core-sw-01").json()[0]

    assert body["matched"] is False
    assert body["device_id"] is None
    assert body["snapshot_id"] is not None
    assert "台帳にありません" in body["message"]


def test_identical_content_does_not_create_a_new_generation(client, device_payload):
    seed_fleet(client, device_payload)

    first = upload(client, "core-sw-01").json()[0]
    second = upload(client, "core-sw-01").json()[0]

    assert second["unchanged"] is True
    assert second["snapshot_id"] == first["snapshot_id"]
    assert len(client.get(f"{API}/configs").json()) == 1


def test_config_generations_can_be_diffed(client, device_payload):
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01")

    changed = load("core-sw-01").decode("utf-8").replace("name OFFICE", "name OFFICE-NEW")
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", changed.encode("utf-8"), "text/plain"))],
    )

    snapshots = client.get(f"{API}/configs", params={"device_id": ids["core"]}).json()
    assert len(snapshots) == 2

    diff = client.get(f"{API}/configs/{snapshots[0]['id']}/diff").json()
    assert diff["changed"] is True
    assert "- name OFFICE" in diff["diff"]
    assert "+ name OFFICE-NEW" in diff["diff"]


def test_first_generation_has_no_diff(client, device_payload):
    seed_fleet(client, device_payload)
    snapshot_id = upload(client, "core-sw-01").json()[0]["snapshot_id"]

    diff = client.get(f"{API}/configs/{snapshot_id}/diff").json()
    assert diff["changed"] is False


def test_shift_jis_config_is_accepted(client, device_payload):
    seed_fleet(client, device_payload)
    body = upload(client, "core-sw-01", encoding="cp932").json()[0]

    assert body["matched"] is True
    interfaces = client.get(f"{API}/configs/devices/{body['device_id']}/interfaces").json()
    # 日本語の description が化けずに読める
    assert any(i["description"] == "未使用" for i in interfaces)


def test_preview_does_not_store_anything(client, device_payload):
    seed_fleet(client, device_payload)
    res = client.post(
        f"{API}/configs/preview",
        files={"file": ("core-sw-01.txt", load("core-sw-01"), "text/plain")},
    )

    assert res.status_code == 200
    assert res.json()["interface_count"] == 4
    assert res.json()["snapshot_id"] is None
    assert client.get(f"{API}/configs").json() == []


def test_device_can_be_specified_when_hostname_is_missing(client, device_payload):
    device_id = add_device(client, device_payload, hostname="mystery-sw")
    res = client.post(
        f"{API}/configs/upload",
        files=[
            ("files", ("dump.txt", b"interface GigabitEthernet0/1\n description X\n", "text/plain"))
        ],
        data={"device_id": str(device_id)},
    )

    body = res.json()[0]
    assert body["device_id"] == device_id
    assert body["interface_count"] == 1


def test_interfaces_removed_from_config_are_dropped(client, device_payload):
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01")

    trimmed = "hostname core-sw-01\ninterface GigabitEthernet1/0/1\n description only-one\n"
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", trimmed.encode("utf-8"), "text/plain"))],
    )

    interfaces = client.get(f"{API}/configs/devices/{ids['core']}/interfaces").json()
    assert [i["name"] for i in interfaces] == ["GigabitEthernet1/0/1"]


def test_unknown_device_id_is_rejected(client):
    res = client.post(
        f"{API}/configs/upload",
        files=[("files", ("a.txt", b"hostname x", "text/plain"))],
        data={"device_id": "999"},
    )
    assert res.status_code == 422


def test_coarser_observed_value_is_not_treated_as_a_difference(client, device_payload):
    """Config 側の情報が粗いだけの場合は、適用して台帳を劣化させない。"""
    add_device(client, device_payload, hostname="acc-sw-osaka-01", os_version="15.2(7)E6")

    body = upload(client, "acc-sw-osaka-01", apply_inventory=True).json()[0]
    version = next(d for d in body["diffs"] if d["field"] == "os_version")

    assert version["current"] == "15.2(7)E6"
    assert version["observed"] == "15.2"
    assert version["differs"] is False
    assert "OSバージョン" not in body["applied_fields"]

    device = client.get(f"{API}/devices/{body['device_id']}").json()
    assert device["os_version"] == "15.2(7)E6"


def test_genuine_version_change_is_still_detected(client, device_payload):
    add_device(client, device_payload, hostname="acc-sw-osaka-01", os_version="12.2(55)SE")

    body = upload(client, "acc-sw-osaka-01").json()[0]
    version = next(d for d in body["diffs"] if d["field"] == "os_version")
    assert version["differs"] is True


# ---------------------------------------------------------------------------
# 台帳が空の状態から実 Config で立ち上げる
# ---------------------------------------------------------------------------


def test_create_missing_builds_the_ledger_from_configs(client):
    """台帳が空でも、Config を入れれば機器が登録される。"""
    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
        data={"create_missing": "true"},
    ).json()[0]

    assert result["created"] is True
    assert result["matched"] is True

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["core-sw-01"]
    assert devices[0]["vendor"] == "Cisco"
    assert devices[0]["model"] == "C9500-40X"


def test_create_missing_is_off_by_default(client):
    """既定では作らない（意図しない登録を防ぐ）。"""
    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
    ).json()[0]

    assert result["created"] is False
    assert client.get(f"{API}/devices").json() == []


def test_create_missing_does_not_duplicate_existing_devices(client, device_payload):
    """既にある機器は作り直さない。"""
    client.post(f"{API}/devices", json=device_payload(hostname="core-sw-01"))
    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
        data={"create_missing": "true"},
    ).json()[0]

    assert result["created"] is False
    assert result["matched"] is True
    assert len(client.get(f"{API}/devices").json()) == 1


def test_create_missing_does_not_overwrite_existing_values(client, device_payload):
    """新規作成は許すが、既存の値の上書きは従来どおり apply_inventory 任せ。"""
    client.post(
        f"{API}/devices",
        json=device_payload(hostname="core-sw-01", model="手で入れた型番"),
    )
    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"

    client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
        data={"create_missing": "true"},
    )

    assert client.get(f"{API}/devices").json()[0]["model"] == "手で入れた型番"


def test_config_without_a_model_is_not_registered(client):
    """型番が読めない Config は登録しない。EoL も脆弱性も型番で引くため。"""
    running_only = b"hostname plain-sw\n!\ninterface GigabitEthernet1/0/1\n description x\n"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("plain-sw.txt", running_only, "text/plain"))],
        data={"create_missing": "true"},
    ).json()[0]

    assert result["created"] is False
    assert client.get(f"{API}/devices").json() == []
    # 黙って落とさず、何をすればいいかを返す
    assert "show version" in result["message"]


def test_topology_builds_itself_from_multiple_configs(client):
    """複数台まとめて入れれば、台帳とトポロジが一度に立ち上がる。"""
    directory = Path(__file__).parent / "fixtures"
    files = [
        ("files", (path.name, path.read_bytes(), "text/plain"))
        for path in sorted(directory.glob("*.txt"))
    ]

    results = client.post(
        f"{API}/configs/upload", files=files, data={"create_missing": "true"}
    ).json()
    created = [r for r in results if r["created"]]
    assert len(created) >= 2

    client.post(f"{API}/topology/rebuild")
    graph = client.get(f"{API}/topology").json()
    assert len(graph["nodes"]) == len(created)
    assert len(graph["edges"]) >= 1
