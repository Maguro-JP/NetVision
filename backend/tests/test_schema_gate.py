"""表の更新は専用ページ経由で、控えが取れてから当たること。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent


def _run(database_url: str, code: str, extra_env: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["NETVISION_DATABASE_URL"] = database_url
    env["NETVISION_SKIP_SCHEMA"] = "0"
    env["PYTHONPATH"] = str(BACKEND)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"失敗しました:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def _prepare_old_db(url: str, extra_env: dict[str, str]) -> None:
    _run(
        url,
        "from alembic import command; from app.schema_alembic import alembic_config; "
        "command.upgrade(alembic_config(), 'b7e4d12c90aa')",
        extra_env,
    )
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO sites (name) VALUES ('本社')"))
        conn.execute(
            text(
                "INSERT INTO devices (hostname, vendor, model, device_type, status) "
                "VALUES ('core-sw-01', 'Cisco', 'C9500', 'switch', 'active')"
            )
        )


def _client_code(script: str) -> str:
    indented = "\n".join(f"    {line}" if line else "" for line in script.strip("\n").split("\n"))
    return (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "import json\n"
        "with TestClient(app) as c:\n"
        f"{indented}\n"
    )


def test_empty_database_is_ready_without_a_backup_page(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    extra = {"NETVISION_BACKUP_PATH": str(tmp_path / "backups")}
    out = _run(
        url,
        _client_code("print(json.dumps(c.get('/api/v1/schema').json()))"),
        extra,
    )
    body = json.loads(out)
    assert body["phase"] == "ready"
    assert body["pending"] == []
    assert not list((tmp_path / "backups").glob("*")) or not any((tmp_path / "backups").iterdir())


def test_outdated_schema_opens_the_migration_gate(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    extra = {
        "NETVISION_BACKUP_PATH": str(tmp_path / "backups"),
        "NETVISION_DOCUMENT_STORAGE_PATH": str(tmp_path / "documents"),
    }
    _prepare_old_db(url, extra)
    out = _run(
        url,
        _client_code(
            "s = c.get('/api/v1/schema')\n"
            "d = c.get('/api/v1/devices')\n"
            "print(json.dumps({'schema': s.json(), 'devices_status': d.status_code, "
            "'hint': d.json().get('hint')}))"
        ),
        extra,
    )
    body = json.loads(out)
    assert body["schema"]["phase"] == "needs_apply"
    assert body["schema"]["pending"]
    assert body["schema"]["pending"][-1]["revision"] == "c9f1a2b3d4e5"
    assert body["devices_status"] == 409
    assert body["hint"] == "schema_migration_required"


def test_apply_copies_the_old_db_then_upgrades(tmp_path: Path):
    """控えは更新前の形。本体は更新後の形。確認するまで控えは残る。"""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "note.txt").write_text("原本", encoding="utf-8")
    extra = {
        "NETVISION_BACKUP_PATH": str(tmp_path / "backups"),
        "NETVISION_DOCUMENT_STORAGE_PATH": str(docs),
    }
    _prepare_old_db(url, extra)

    out = _run(
        url,
        _client_code(
            "applied = c.post('/api/v1/schema/apply')\n"
            "sites = c.get('/api/v1/sites')\n"
            "print(json.dumps({'apply': applied.status_code, 'body': applied.json(), "
            "'sites_status': sites.status_code, "
            "'site_count': len(sites.json()) if sites.status_code == 200 else None}))"
        ),
        extra,
    )
    body = json.loads(out)
    assert body["apply"] == 200
    status = body["body"]
    assert status["phase"] == "needs_confirm"
    assert status["unconfirmed"]
    assert body["sites_status"] == 200
    assert body["site_count"] == 1
    assert status["counts"]["devices"] == 1

    backup_path = Path(status["unconfirmed"][0]["path"])
    assert backup_path.is_dir()
    copied = backup_path / "live.db"
    assert copied.is_file()
    assert (backup_path / "documents" / "note.txt").read_text(encoding="utf-8") == "原本"

    backup_engine = create_engine(f"sqlite:///{copied}")
    backup_cols = {c["name"] for c in inspect(backup_engine).get_columns("topology_nodes")}
    assert "site_id" not in backup_cols

    live_cols = {c["name"] for c in inspect(create_engine(url)).get_columns("topology_nodes")}
    assert "site_id" in live_cols

    confirm_out = _run(
        url,
        _client_code("r = c.post('/api/v1/schema/confirm')\nprint(json.dumps(r.json()))"),
        extra,
    )
    confirmed = json.loads(confirm_out)
    assert confirmed["phase"] == "ready"
    assert confirmed["unconfirmed"] == []
    assert copied.is_file()
    assert (backup_path / "documents" / "note.txt").is_file()
