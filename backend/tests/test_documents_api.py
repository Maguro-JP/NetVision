import pytest

from app.services import documents as service
from tests.conftest import API


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """テストごとにファイル保管領域を分ける。"""
    from app.config import settings

    monkeypatch.setattr(settings, "document_storage_path", str(tmp_path / "documents"))
    yield


def add_document(client, *, file: bytes | None = None, **fields):
    data = {"vendor": "Cisco", "title": "コマンドリファレンス"}
    data.update({k: v for k, v in fields.items() if v is not None})
    files = {"file": ("manual.pdf", file, "application/pdf")} if file else None
    return client.post(f"{API}/documents", data=data, files=files)


def add_device(client, device_payload, **overrides):
    return client.post(f"{API}/devices", json=device_payload(**overrides)).json()["id"]


# ---------------------------------------------------------------------------
# 登録と保管
# ---------------------------------------------------------------------------


def test_document_with_file_is_stored(client):
    res = add_document(
        client,
        file=b"%PDF-1.4 dummy manual",
        model="C9500-40X",
        os_name="IOS-XE",
        applies_from="17.9.0",
        applies_to="17.9.99",
        version_label="17.9.x",
        source_url="https://cisco.com/c9500/command-reference-17-9.pdf",
    )
    assert res.status_code == 201

    body = res.json()
    assert body["has_file"] is True
    assert body["filename"] == "manual.pdf"
    assert body["size_bytes"] == len(b"%PDF-1.4 dummy manual")
    assert body["version_label"] == "17.9.x"

    download = client.get(f"{API}/documents/{body['id']}/download")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4 dummy manual"


def test_document_without_file_is_allowed(client):
    """実体が無く、URL だけの登録もできる（社内サーバー参照など）。"""
    body = add_document(client, source_url="https://cisco.com/doc").json()

    assert body["has_file"] is False
    assert client.get(f"{API}/documents/{body['id']}/download").status_code == 404


def test_identical_files_share_one_stored_copy(client):
    first = add_document(client, file=b"same bytes", title="版A").json()
    second = add_document(client, file=b"same bytes", title="版B").json()

    assert first["sha256"] == second["sha256"]
    # 実体は1つだけ
    stored = list((service.storage_root()).rglob("*.pdf"))
    assert len(stored) == 1


def test_deleting_one_document_keeps_the_shared_file(client):
    first = add_document(client, file=b"same bytes", title="版A").json()
    second = add_document(client, file=b"same bytes", title="版B").json()

    client.delete(f"{API}/documents/{first['id']}")

    # まだ版B が参照しているのでファイルは残る
    assert client.get(f"{API}/documents/{second['id']}/download").status_code == 200


def test_oversized_file_is_rejected(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_document_bytes", 10)
    res = add_document(client, file=b"x" * 100)
    assert res.status_code == 413


# ---------------------------------------------------------------------------
# 古い版を消さない（ここが要件の核心）
# ---------------------------------------------------------------------------


def test_new_edition_does_not_remove_the_old_one(client):
    old = add_document(
        client, title="コマンドリファレンス 17.9", applies_from="17.9.0", applies_to="17.9.99"
    ).json()
    new = add_document(
        client, title="コマンドリファレンス 17.12", applies_from="17.12.0", applies_to="17.12.99"
    ).json()

    client.post(f"{API}/documents/{old['id']}/supersede/{new['id']}")

    documents = client.get(f"{API}/documents").json()
    # 旧版は消えず、後継が示されるだけ
    assert len(documents) == 2
    old_after = next(d for d in documents if d["id"] == old["id"])
    assert old_after["superseded_by_id"] == new["id"]
    assert old_after["superseded_by_title"] == "コマンドリファレンス 17.12"


def test_superseded_documents_are_listed_by_default(client):
    old = add_document(client, title="旧版").json()
    new = add_document(client, title="新版").json()
    client.post(f"{API}/documents/{old['id']}/supersede/{new['id']}")

    assert len(client.get(f"{API}/documents").json()) == 2
    hidden = client.get(f"{API}/documents", params={"include_superseded": "false"}).json()
    assert [d["title"] for d in hidden] == ["新版"]


def test_a_document_cannot_supersede_itself(client):
    doc = add_document(client).json()
    assert client.post(f"{API}/documents/{doc['id']}/supersede/{doc['id']}").status_code == 422


def test_old_version_device_still_finds_its_manual(client, device_payload):
    """17.9 で動いている機器は、新版が出た後も 17.9 向けの資料を引ける。"""
    old = add_document(
        client,
        title="コマンドリファレンス 17.9",
        model="C9500-40X",
        applies_from="17.9.0",
        applies_to="17.9.99",
    ).json()
    new = add_document(
        client,
        title="コマンドリファレンス 17.12",
        model="C9500-40X",
        applies_from="17.12.0",
        applies_to="17.12.99",
    ).json()
    client.post(f"{API}/documents/{old['id']}/supersede/{new['id']}")

    device_id = add_device(client, device_payload, model="C9500-40X", os_version="17.09.04a")

    found = client.get(f"{API}/devices/{device_id}/documents").json()
    assert [d["title"] for d in found] == ["コマンドリファレンス 17.9"]
    assert "17.09.04a" in found[0]["reason"]


# ---------------------------------------------------------------------------
# 機器からの逆引き
# ---------------------------------------------------------------------------


def test_version_range_filters_documents(client, device_payload):
    add_document(
        client, title="17.9向け", model="C9500-40X", applies_from="17.9.0", applies_to="17.9.99"
    )
    add_document(
        client, title="17.12向け", model="C9500-40X", applies_from="17.12.0", applies_to="17.12.99"
    )
    add_document(client, title="全バージョン共通のハード資料", model="C9500-40X")

    device_id = add_device(client, device_payload, model="C9500-40X", os_version="17.12.03")

    titles = [d["title"] for d in client.get(f"{API}/devices/{device_id}/documents").json()]
    assert set(titles) == {"17.12向け", "全バージョン共通のハード資料"}


def test_model_wildcard_and_series_documents(client, device_payload):
    add_document(client, title="C9500シリーズ共通", model="C9500*")
    add_document(client, title="Cisco全般", model=None)
    add_document(client, title="別機種向け", model="C9300-48P")

    device_id = add_device(client, device_payload, model="C9500-40X")

    titles = [d["title"] for d in client.get(f"{API}/devices/{device_id}/documents").json()]
    assert set(titles) == {"C9500シリーズ共通", "Cisco全般"}
    # 型番指定のある資料が先に来る
    assert titles[0] == "C9500シリーズ共通"


def test_other_vendor_documents_are_excluded(client, device_payload):
    add_document(client, vendor="Juniper", title="Junos リファレンス")
    device_id = add_device(client, device_payload, vendor="Cisco")

    assert client.get(f"{API}/devices/{device_id}/documents").json() == []


def test_device_without_version_only_gets_common_documents(client, device_payload):
    add_document(client, title="17.9向け", applies_from="17.9.0", applies_to="17.9.99")
    add_document(client, title="バージョン共通")

    device_id = add_device(client, device_payload, os_version=None)

    titles = [d["title"] for d in client.get(f"{API}/devices/{device_id}/documents").json()]
    assert titles == ["バージョン共通"]


def test_document_reports_how_many_devices_it_covers(client, device_payload):
    add_document(client, title="C9500向け", model="C9500-40X")
    add_device(client, device_payload, hostname="sw-1", model="C9500-40X")
    add_device(client, device_payload, hostname="sw-2", model="C9500-40X", serial_number="X2")
    add_device(client, device_payload, hostname="sw-3", model="C9300-48P", serial_number="X3")

    doc = client.get(f"{API}/documents").json()[0]
    assert doc["device_count"] == 2


def test_documents_can_be_searched(client):
    add_document(client, title="コマンドリファレンス", model="C9500-40X")
    add_document(client, title="リリースノート", model="MX204", vendor="Juniper")

    assert len(client.get(f"{API}/documents", params={"q": "リリース"}).json()) == 1
    assert len(client.get(f"{API}/documents", params={"vendor": "Juniper"}).json()) == 1
    assert len(client.get(f"{API}/documents", params={"q": "C9500"}).json()) == 1
