"""マイグレーションで作ったスキーマが、実際に使えること。

`Base.metadata.create_all` で作った DB ではテストが通るのに、alembic で作った DB では
落ちる、という食い違いが起きうる。両方が同じ結果になることは自動では保証されない。

実際に `server_default=sa.text("now()")` と書いてしまい、SQLite では
`unknown function: now()` で挿入できない状態になっていた。Postgres では通るので
気づきにくい。ここで通しておく。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent


def _run_backend(database_url: str, code: str) -> None:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["NETVISION_DATABASE_URL"] = database_url
    env["NETVISION_SKIP_SCHEMA"] = "0"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(BACKEND) if not existing else f"{BACKEND}{os.pathsep}{existing}"
    result = subprocess.run(
        [sys.executable, "-c", code],
        # backend/ を cwd にすると、同名の alembic/ がライブラリを隠す
        cwd=BACKEND.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"スキーマ適用に失敗しました:\n{result.stdout}\n{result.stderr}")


def _upgrade(database_url: str) -> None:
    _run_backend(database_url, "from app.bootstrap import apply_schema; apply_schema()")


@pytest.fixture
def migrated(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    _upgrade(url)
    return url


def test_every_table_accepts_a_row(migrated: str):
    """既定値が壊れていないこと。

    `now()` のように、その DB に無い関数を既定値に書くと、テーブルは作れるのに
    挿入の時点で初めて落ちる。作れたことは動くことを意味しない。
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


def _site_id_and_revision(url: str) -> tuple[bool, str | None]:
    engine = create_engine(url)
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("topology_nodes")}
    with engine.connect() as conn:
        revision = conn.execute(text("select version_num from alembic_version")).scalar()
    return "site_id" in columns, revision


def test_apply_schema_is_safe_to_run_twice(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    _upgrade(url)
    _upgrade(url)
    has_site, revision = _site_id_and_revision(url)
    assert has_site
    assert revision


def test_create_all_db_without_alembic_version_gets_stamped(tmp_path: Path):
    """以前の1台完結は create_all だったので、版の記録が無い。

    列はモデルどおりにある。stamp してから差分を当てれば、手で SQL を書く必要は無い。
    """
    url = f"sqlite:///{tmp_path / 'create_all.db'}"
    _run_backend(
        url,
        "import app.models; from app.db import Base, engine; Base.metadata.create_all(engine)",
    )
    engine = create_engine(url)
    assert "alembic_version" not in inspect(engine).get_table_names()

    _upgrade(url)
    has_site, revision = _site_id_and_revision(url)
    assert has_site
    assert revision


def test_old_sqlite_without_site_id_gains_the_column(tmp_path: Path):
    """site_id を足す前の試用 DB を起動したとき、列が自動で付くこと。"""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    _run_backend(
        url,
        "from alembic import command; from app.bootstrap import _alembic_config; "
        "command.upgrade(_alembic_config(), 'b7e4d12c90aa')",
    )
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("drop table alembic_version"))
    columns = {c["name"] for c in inspect(engine).get_columns("topology_nodes")}
    assert "site_id" not in columns

    _upgrade(url)
    has_site, revision = _site_id_and_revision(url)
    assert has_site
    assert revision == "d3a7c1e5b982"


def test_existing_rows_stay_as_global_layout(tmp_path: Path):
    """既存の座標は全体図（site_id 空）のまま残る。人が移し替える作業は無い。"""
    url = f"sqlite:///{tmp_path / 'rows.db'}"
    _run_backend(
        url,
        "from alembic import command; from app.bootstrap import _alembic_config; "
        "command.upgrade(_alembic_config(), 'b7e4d12c90aa')",
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
        device_id = conn.execute(text("SELECT id FROM devices")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO topology_nodes (device_id, layer, x, y, pinned) "
                "VALUES (:d, 'physical', 120, 80, 1)"
            ),
            {"d": device_id},
        )
        conn.execute(text("drop table alembic_version"))

    _upgrade(url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT site_id, x, y FROM topology_nodes WHERE device_id = :d"),
            {"d": device_id},
        ).one()
    assert row[0] is None
    assert row[1] == 120
    assert row[2] == 80
