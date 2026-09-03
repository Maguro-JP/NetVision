"""控えが取れていること、そして本当に開けることまで見る。

「取れているつもりで戻せない」が一番困るので、取ったものを開いて
中身を数えるところまでを確かめる。戻す操作は持たない（人が手順で行う）。
"""

from pathlib import Path

import pytest

from tests.conftest import API


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """控えの対象になる、ファイルの DB と資料フォルダを用意する。

    試験の API はメモリ上の DB を使うので、控えを取る対象は別に置く。
    ここで見たいのは「ファイルを正しく写して、開けることを確かめられるか」。
    """
    import sqlite3

    from app.config import settings

    database = tmp_path / "netvision.db"
    connection = sqlite3.connect(database)
    connection.execute("create table devices (id integer primary key, hostname text)")
    connection.execute("create table sites (id integer primary key, name text)")
    connection.execute("insert into devices (hostname) values ('core-01')")
    connection.commit()
    connection.close()

    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "note.txt").write_text("資料", encoding="utf-8")

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database}")
    # 控えを取る前に WAL を畳む処理は、動いているつなぎを使う。
    # 試験でも、控えの対象と同じファイルを指させる
    from sqlalchemy import create_engine

    import app.db as db_module

    monkeypatch.setattr(db_module, "engine", create_engine(f"sqlite:///{database}"))
    monkeypatch.setattr(settings, "document_storage_path", str(documents))
    monkeypatch.setattr(settings, "backup_path", str(tmp_path / "backups"))
    return tmp_path


def test_taking_a_backup_also_checks_that_it_opens(client):
    res = client.post(f"{API}/backups")
    assert res.status_code == 200, res.text
    body = res.json()
    latest = body["latest"]
    assert latest is not None
    assert latest["verified"] is True, latest["verify_message"]
    assert "devices 1 件" in latest["verify_message"]
    assert (Path(latest["path"]) / "documents" / "note.txt").is_file()
    assert Path(latest["path"]).is_dir()
    assert latest["bytes"] > 0


def test_the_listing_shows_what_is_there(client):
    client.post(f"{API}/backups")
    client.post(f"{API}/backups")
    body = client.get(f"{API}/backups").json()
    assert len(body["backups"]) == 2
    assert all(item["exists"] for item in body["backups"])
    # 新しいものが先頭
    assert body["backups"][0]["created_at"] >= body["backups"][1]["created_at"]
    assert "docs/OPERATIONS.md" in body["restore_hint"]


def test_a_broken_backup_is_reported_as_not_usable(client, data_dir):
    from app.services import backup

    client.post(f"{API}/backups")
    folder = Path(client.get(f"{API}/backups").json()["latest"]["path"])
    target = next(folder.glob("*.db"), None) or next(folder.glob("*.sqlite*"))
    target.write_bytes(b"this is not a database")

    result = backup.verify(folder)
    assert result.ok is False
    assert result.message.startswith("控えの DB を")  # 開けない / 読めない / 壊れている


def test_a_missing_backup_folder_is_reported(client):
    from app.services import backup

    assert backup.verify(Path("/nonexistent/backup")).ok is False


def test_old_backups_are_cleared_but_the_newest_stay(client, monkeypatch):
    from app.services import appsettings

    monkeypatch.setattr(appsettings, "load", lambda: {"backup_keep": 2})
    for _ in range(4):
        client.post(f"{API}/backups")
    alive = [item for item in client.get(f"{API}/backups").json()["backups"] if item["exists"]]
    assert len(alive) == 2


def test_the_interval_is_set_from_the_screen(client):
    assert client.get(f"{API}/settings").json()["backup_interval_hours"] == 24
    assert client.patch(f"{API}/settings", json={"backup_interval_hours": 6}).status_code == 200
    body = client.get(f"{API}/backups").json()
    assert body["interval_hours"] == 6

    # 0 で自動では取らなくなる
    client.patch(f"{API}/settings", json={"backup_interval_hours": 0})
    assert client.get(f"{API}/backups").json()["next_run_at"] is None

    assert client.patch(f"{API}/settings", json={"backup_interval_hours": 9999}).status_code == 422
    assert client.patch(f"{API}/settings", json={"backup_keep": 0}).status_code == 422


def test_the_backup_taken_before_a_schema_change_is_not_cleared(client, monkeypatch):
    from app.services import appsettings, schema_gate

    monkeypatch.setattr(appsettings, "load", lambda: {"backup_keep": 1})
    kept = schema_gate._create_backup_folder("abc123", "def456")
    for _ in range(3):
        client.post(f"{API}/backups")

    items = client.get(f"{API}/backups").json()["backups"]
    protected = next(item for item in items if item["id"] == kept["id"])
    assert protected["exists"] is True
    assert protected["before_schema_change"] is True
