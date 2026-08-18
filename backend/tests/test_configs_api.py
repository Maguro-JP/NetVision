from pathlib import Path

from tests.conftest import API

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / f"{name}.txt").read_bytes()


def upload(
    client, *names, apply_inventory=False, device_id=None, encoding=None, create_missing=None
):
    files = [
        ("files", (f"{name}.txt", _encode(load(name), encoding), "text/plain")) for name in names
    ]
    data = {"apply_inventory": str(apply_inventory).lower()}
    if create_missing is not None:
        data["create_missing"] = str(create_missing).lower()
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
    """登録しない指定のときも、読んだ内容は捨てない。

    後から機器を登録すれば紐付けられるので、取り込み直しは要らない。
    """
    body = upload(client, "core-sw-01", create_missing=False).json()[0]

    assert body["matched"] is False
    assert body["device_id"] is None
    assert body["snapshot_id"] is not None
    assert "台帳にありません" in body["message"]


def test_unmatched_hostname_can_match_by_serial(client, device_payload):
    """機器名が変わっていても、シリアルが同じなら同一機として紐付ける。"""
    device_id = add_device(
        client,
        device_payload,
        hostname="qx-old-name",
        vendor="NEC",
        serial_number="210235A0L6C123000010",
    )

    body = upload(client, "qx-sw-01", create_missing=False).json()[0]

    assert body["matched"] is True
    assert body["device_id"] == device_id
    assert body["hostname"] == "qx-sw-01"
    diffs = {d["field"]: d for d in body["diffs"]}
    assert diffs["serial_number"]["observed"] == "210235A0L6C123000010"
    assert diffs["serial_number"]["differs"] is False


def test_fqdn_inventory_hostname_matches_short_sysname(client, device_payload):
    """台帳が FQDN でも、Config の短いホスト名で同一機とみなす。"""
    device_id = add_device(client, device_payload, hostname="core-sw-01.example.com")

    body = upload(client, "core-sw-01", create_missing=False).json()[0]

    assert body["matched"] is True
    assert body["device_id"] == device_id


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


def test_uploading_a_config_registers_the_device_by_default(client):
    """何も指定しなくても、取り込めば機器一覧に載る。

    ここが既定で off だったせいで、Config を入れても一覧に何も出てこず、
    連携していないように見えていた。無い物を足すのは上書きではないので、
    既定で行う。人が入れた値を書き換える apply_inventory とは分けてある。
    """
    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes(), "text/plain"))],
    ).json()[0]

    assert result["created"] is True
    assert [d["hostname"] for d in client.get(f"{API}/devices").json()] == ["core-sw-01"]


def test_existing_values_are_not_overwritten_by_default(client):
    """既定では、人が入れた台帳の値を書き換えない。"""
    client.post(
        f"{API}/devices",
        json={"hostname": "core-sw-01", "vendor": "Cisco", "model": "手で入れた型番"},
    )

    upload(client, "core-sw-01")

    device = client.get(f"{API}/devices").json()[0]
    assert device["model"] == "手で入れた型番"


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


def test_config_without_a_model_is_registered_and_flagged(client):
    """型番が読めなくても台帳に載せる。ただし何が足りないかを示す。

    running-config だけでは型番が読めないことが多い。これを理由に登録を
    見送ると、取り込んだのに機器一覧に何も出てこない。利用者から見れば
    「何も起きなかった」であって、道具として使えない。

    載せたうえで足りない印を付ける。型番は EoL と脆弱性を引く鍵なので、
    埋めてもらう必要は変わらないが、埋める対象が見えている方が直せる。
    """
    running_only = b"hostname plain-sw\n!\ninterface GigabitEthernet1/0/1\n description x\n"

    result = client.post(
        f"{API}/configs/upload",
        files=[("files", ("plain-sw.txt", running_only, "text/plain"))],
        data={"create_missing": "true"},
    ).json()[0]

    assert result["created"] is True

    devices = client.get(f"{API}/devices").json()
    assert [d["hostname"] for d in devices] == ["plain-sw"]
    # 足りない項目は機器自身が答える。一覧で「情報不足」として出す
    assert "型番" in devices[0]["missing_fields"]
    # 黙って埋めない。空のまま載せる
    assert devices[0]["model"] == ""

    # 何をすればいいかも返す
    assert "show version" in result["message"]


def test_a_config_is_linked_by_the_hostname_inside_it_not_the_filename(client):
    """紐付けはファイル名ではなく Config の中のホスト名で決める。

    ファイル名は現場で自由に付く（日付や作業番号が入る）。ファイル名に
    合わせろという作りは、道具の都合を人に押し付けている。
    """
    client.post(
        f"{API}/devices",
        json={"hostname": "core-sw-01", "vendor": "Cisco", "model": "C9300-48P"},
    )

    result = client.post(
        f"{API}/configs/upload",
        files=[
            (
                "files",
                (
                    "20260810_作業3_backup.txt",
                    b"hostname core-sw-01\n!\ninterface GigabitEthernet1/0/1\n",
                    "text/plain",
                ),
            )
        ],
    ).json()[0]

    assert result["matched"] is True
    assert result["device_hostname"] == "core-sw-01"
    assert result["created"] is False


def test_topology_builds_itself_from_multiple_configs(client):
    """複数台まとめて入れれば、台帳とトポロジーが一度に立ち上がる。"""
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


def test_the_same_config_still_updates_the_inventory(client, device_payload):
    """内容が同じでも台帳の更新は行う。

    世代を増やさないのと、読み取った値を台帳へ反映しないのは別の話。
    「同じ内容だから」で更新されないのは、使う側から見れば反映漏れにしか
    見えない。2回目の取り込みで初めて反映を指定することもある。
    """
    ids = seed_fleet(client, device_payload)

    first = upload(client, "core-sw-01").json()[0]
    assert first["unchanged"] is False
    assert client.get(f"{API}/devices/{ids['core']}").json()["serial_number"] is None

    # 同じ内容を、今度は台帳へ反映する指定で
    second = upload(client, "core-sw-01", apply_inventory=True).json()[0]

    assert second["unchanged"] is True  # 世代は増えない
    assert second["snapshot_id"] == first["snapshot_id"]
    assert len(client.get(f"{API}/configs").json()) == 1
    # 台帳はちゃんと更新される
    assert "シリアル番号" in second["applied_fields"]
    assert client.get(f"{API}/devices/{ids['core']}").json()["serial_number"] == "FOC2401A001"


def test_the_same_config_keeps_ports_and_links_in_place(client, device_payload):
    """同じ内容の取り込みで、ポートや結線が消えないこと。"""
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01")
    before = client.get(f"{API}/configs/devices/{ids['core']}/interfaces").json()

    upload(client, "core-sw-01")

    after = client.get(f"{API}/configs/devices/{ids['core']}/interfaces").json()
    assert [i["name"] for i in after] == [i["name"] for i in before]
    assert after


def test_a_config_taken_in_first_is_adopted_when_the_device_is_registered(client, device_payload):
    """先に Config、あとから機器。この順でも紐付く。

    台帳から作る人もいれば、手元の Config をまとめて入れてから機器を
    整える人もいる。後者で取り込み直しが要るのは筋が通らない。
    """
    # 登録せずに取り込む（宙に浮いた状態を作る）
    first = upload(client, "core-sw-01", create_missing=False).json()[0]
    assert first["matched"] is False
    assert first["device_id"] is None

    device_id = add_device(client, device_payload, hostname="core-sw-01")

    # 取り込み直さなくても、機器に紐付いている
    snapshots = client.get(f"{API}/configs", params={"device_id": device_id}).json()
    assert [s["id"] for s in snapshots] == [first["snapshot_id"]]

    # 中身も入っている。紐付けだけして空のままでは意味がない
    interfaces = client.get(f"{API}/configs/devices/{device_id}/interfaces").json()
    assert len(interfaces) == 4


def test_adoption_matches_hostnames_case_insensitively(client, device_payload):
    """ホスト名の大文字小文字やドメイン付きの差は吸収する。"""
    upload(client, "core-sw-01", create_missing=False)
    device_id = add_device(client, device_payload, hostname="CORE-SW-01")

    assert client.get(f"{API}/configs", params={"device_id": device_id}).json()


def test_registering_an_unrelated_device_adopts_nothing(client, device_payload):
    """関係ない機器を登録しても、他人の Config を引き取らない。"""
    upload(client, "core-sw-01", create_missing=False)
    other = add_device(client, device_payload, hostname="unrelated-sw-99")

    assert client.get(f"{API}/configs", params={"device_id": other}).json() == []
