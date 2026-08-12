"""変更を戻せること。

手で台帳を直す作業は、戻せないと踏み切れない。「間違えたら終わり」だと、
直すべきところも触れなくなる。
"""

from tests.conftest import API


def latest_change(client, field: str) -> dict:
    items = client.get(f"{API}/changes").json()["items"]
    return next(c for c in items if c["field"] == field)


def test_a_field_edit_can_be_undone(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload(hostname="core-sw-01")).json()[
        "id"
    ]
    client.patch(f"{API}/devices/{device_id}", json={"hostname": "typo-01"})
    assert client.get(f"{API}/devices/{device_id}").json()["hostname"] == "typo-01"

    entry = latest_change(client, "hostname")
    res = client.post(f"{API}/changes/{entry['id']}/revert")

    assert res.status_code == 200
    assert client.get(f"{API}/devices/{device_id}").json()["hostname"] == "core-sw-01"


def test_undoing_is_itself_recorded(client, device_payload):
    """戻したことも履歴に残る。消してしまうと経緯が追えない。"""
    device_id = client.post(f"{API}/devices", json=device_payload()).json()["id"]
    client.patch(f"{API}/devices/{device_id}", json={"notes": "打ち間違い"})

    entry = latest_change(client, "notes")
    client.post(f"{API}/changes/{entry['id']}/revert")

    notes = [c for c in client.get(f"{API}/changes").json()["items"] if c["field"] == "notes"]
    assert len(notes) == 2  # 変更と、戻した分
    assert any("戻す" in (c["note"] or "") for c in notes)


def test_a_date_can_be_undone(client, device_payload):
    """日付も文字列で記録されているので、型を戻せること。"""
    device_id = client.post(f"{API}/devices", json=device_payload(eol_date="2030-03-02")).json()[
        "id"
    ]
    client.patch(f"{API}/devices/{device_id}", json={"eol_date": "2028-01-01"})

    entry = latest_change(client, "eol_date")
    client.post(f"{API}/changes/{entry['id']}/revert")

    assert client.get(f"{API}/devices/{device_id}").json()["eol_date"] == "2030-03-02"


def test_clearing_a_value_can_be_undone(client, device_payload):
    device_id = client.post(f"{API}/devices", json=device_payload(rack="A-01")).json()["id"]
    client.patch(f"{API}/devices/{device_id}", json={"rack": None})

    entry = latest_change(client, "rack")
    client.post(f"{API}/changes/{entry['id']}/revert")

    assert client.get(f"{API}/devices/{device_id}").json()["rack"] == "A-01"


def test_undoing_onto_a_name_someone_else_took_is_refused(client, device_payload):
    """戻すと重複する場合は、理由を出して止める。"""
    first = client.post(f"{API}/devices", json=device_payload(hostname="sw-a")).json()["id"]
    client.patch(f"{API}/devices/{first}", json={"hostname": "sw-a2"})
    client.post(f"{API}/devices", json=device_payload(hostname="sw-a", serial_number="X2"))

    entry = latest_change(client, "hostname")
    res = client.post(f"{API}/changes/{entry['id']}/revert")

    assert res.status_code == 422
    assert "他の機器が使っています" in res.json()["detail"]
    assert client.get(f"{API}/devices/{first}").json()["hostname"] == "sw-a2"


def test_a_creation_is_not_undone(client, device_payload):
    """作成は戻さない。消すのは削除であって、戻すとは違う。"""
    client.post(f"{API}/devices", json=device_payload())
    created = next(
        c for c in client.get(f"{API}/changes").json()["items"] if c["action"] == "create"
    )

    res = client.post(f"{API}/changes/{created['id']}/revert")

    assert res.status_code == 422
    assert "作成・削除は戻せません" in res.json()["detail"]
    assert len(client.get(f"{API}/devices").json()) == 1


def test_an_unknown_entry_is_refused(client):
    res = client.post(f"{API}/changes/9999/revert")
    assert res.status_code == 422


def test_a_whole_import_can_be_undone(client, device_payload):
    """取り込み1回分をまとめて戻せること。

    台帳を丸ごと取り込んで結果がおかしかったとき、1件ずつ戻すのは現実的でない。
    """
    device_id = client.post(f"{API}/devices", json=device_payload(hostname="keep-01")).json()["id"]

    csv = "機器ID,ホスト名,ベンダー,型番,備考\n"
    csv += f"{device_id},changed-01,Cisco,C9500-40X,取り込みで書き換え\n"
    client.post(
        f"{API}/import/devices",
        files={"file": ("devices.csv", csv.encode("utf-8"), "text/csv")},
        data={"commit": "true"},
    )
    assert client.get(f"{API}/devices/{device_id}").json()["hostname"] == "changed-01"

    batch = next(
        c["batch_id"] for c in client.get(f"{API}/changes").json()["items"] if c["batch_id"]
    )
    result = client.post(f"{API}/changes/batches/{batch}/revert").json()

    assert result["reverted"] >= 1
    assert client.get(f"{API}/devices/{device_id}").json()["hostname"] == "keep-01"


def test_an_unknown_batch_is_refused(client):
    res = client.post(f"{API}/changes/batches/does-not-exist/revert")
    assert res.status_code == 422
