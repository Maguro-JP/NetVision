"""マイグレーションで作ったスキーマが、実際に使えること。

`Base.metadata.create_all` で作った DB ではテストが通るのに、alembic で作った DB では
落ちる、という食い違いが起きうる。**両方が同じ結果になることは自動では保証されない**。

実際に `server_default=sa.text("now()")` と書いてしまい、SQLite では
`unknown function: now()` で挿入できない状態になっていた。Postgres では通るので
気づきにくい。ここで通しておく。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent


def _upgrade(database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "NETVISION_DATABASE_URL": database_url, "HOME": "/tmp"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade に失敗しました:\n{result.stdout}\n{result.stderr}")


@pytest.fixture
def migrated(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    _upgrade(url)
    return url


def test_every_table_accepts_a_row(migrated: str):
    """既定値が壊れていないこと。

    `now()` のように、その DB に無い関数を既定値に書くと、テーブルは作れるのに
    **挿入の時点で初めて落ちる**。作れたことは動くことを意味しない。
    """
    engine = create_engine(migrated)
    inspector = inspect(engine)

    with engine.begin() as conn:
        for table in inspector.get_table_names():
            if table == "alembic_version":
                continue
            defaults = [
                (column["name"], str(column.get("default") or ""))
                for column in inspector.get_columns(table)
            ]
            for name, default in defaults:
                assert "now()" not in default.lower(), (
                    f"{table}.{name} の既定値 {default} は SQLite で動きません。"
                    "CURRENT_TIMESTAMP を使ってください"
                )

        # 実際に1行入れて、既定値が評価できることまで見る
        conn.execute(text("INSERT INTO sites (name) VALUES ('検証用')"))
        conn.execute(
            text(
                "INSERT INTO devices (hostname, vendor, model, device_type, status) "
                "VALUES ('sw-test', 'Cisco', 'C9300', 'switch', 'active')"
            )
        )
        device_id = conn.execute(text("SELECT id FROM devices")).scalar_one()

        # 既定値に関数を書いてしまいがちな表を、実際に触って確かめる
        conn.execute(
            text("INSERT INTO device_vlans (device_id, vlan_id, name) VALUES (:d, 10, 'USERS')"),
            {"d": device_id},
        )
        conn.execute(
            text("INSERT INTO vlans (vlan_id, name) VALUES (10, '全社ユーザ')"),
        )
        conn.execute(
            text("INSERT INTO device_vrfs (device_id, name) VALUES (:d, 'SHANAI')"),
            {"d": device_id},
        )

        assert conn.execute(text("SELECT updated_at FROM device_vlans")).scalar_one() is not None


def test_schema_matches_the_models(migrated: str):
    """モデルに足した列が、マイグレーションにも入っていること。

    片方だけ直すと、開発環境（create_all）では動いて本番（alembic）で落ちる。
    """
    import app.models  # noqa: F401
    from app.db import Base

    engine = create_engine(migrated)
    inspector = inspect(engine)
    actual = {
        name: {c["name"] for c in inspector.get_columns(name)}
        for name in inspector.get_table_names()
    }

    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in actual:
            missing.append(f"表そのものが無い: {table.name}")
            continue
        for column in table.columns:
            if column.name not in actual[table.name]:
                missing.append(f"{table.name}.{column.name}")

    assert not missing, "マイグレーションに入っていない項目があります: " + ", ".join(missing)
