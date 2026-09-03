"""表の差分を当てる前に控えを取り、確認するまで残す。

人が SQL を流す必要は無い。控えが取れなければ表は更新しない。
確認したあとも控えは消さない。後から問題が出ても戻せるようにする。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.config import settings
from app.services import localtime

logger = logging.getLogger(__name__)

INDEX_NAME = "index.json"
LOCK_NAME = ".apply.lock"
SCHEMA_BLOCK_HINT = "schema_migration_required"


def backup_dir() -> Path:
    if settings.backup_path:
        return Path(settings.backup_path).expanduser().resolve()
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database:
        return Path(url.database).expanduser().resolve().parent / "backups"
    return Path(settings.document_storage_path).expanduser().resolve().parent / "backups"


def _now() -> datetime:
    # 地域の時計が読めない環境でも、表の更新そのものは通す必要がある。
    # 控えのフォルダ名が UTC になるだけで、データは守られる
    return localtime.now()


def _index_path() -> Path:
    return backup_dir() / INDEX_NAME


def _read_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("控えの一覧が壊れているので空として扱います: %s", path)
        return []
    items = payload.get("backups") if isinstance(payload, dict) else payload
    return items if isinstance(items, list) else []


def _write_index(items: list[dict[str, Any]]) -> None:
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"backups": items}
    _index_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def unconfirmed_backups() -> list[dict[str, Any]]:
    return [item for item in _read_index() if not item.get("confirmed_at")]


def folder_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    if not path.is_dir():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += (Path(root) / name).stat().st_size
    return total


def _sqlite_paths() -> tuple[Path | None, list[Path]]:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return None, []
    db = Path(url.database).expanduser().resolve()
    extras = [p for p in (Path(str(db) + "-wal"), Path(str(db) + "-shm")) if p.exists()]
    return db, extras


def _checkpoint_sqlite() -> None:
    from sqlalchemy import text

    from app.db import engine

    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))


def _copy_documents(dest: Path) -> int:
    source = Path(settings.document_storage_path).expanduser().resolve()
    if not source.is_dir():
        return 0
    target = dest / "documents"
    shutil.copytree(source, target, dirs_exist_ok=True)
    return folder_size(target)


def _dump_postgres(dest: Path) -> Path:
    url = make_url(settings.database_url)
    dump = dest / "database.dump"
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError(
            "Postgres の控えを取る pg_dump が見つかりません。"
            "表はまだ更新していません。"
            "コンテナに postgresql-client が入っているか確認してください。"
        )
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    command = [
        pg_dump,
        "--no-owner",
        "--format=custom",
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "netvision",
        "-d",
        url.database or "netvision",
        "-f",
        str(dump),
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode != 0 or not dump.is_file() or dump.stat().st_size == 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "Postgres の控えが取れませんでした。表はまだ更新していません。"
            + (f" {detail}" if detail else "")
        )
    return dump


def create_backup(reason: str) -> dict[str, Any]:
    """表の更新とは関係なく、いまの状態の控えを取る。

    定期実行（backup.py）から呼ぶ。取れなかったときは例外を投げる。
    黙って「取れたことにする」のが一番まずい。
    """
    return _create_backup_folder(None, reason)


def _create_backup_folder(from_revision: str | None, to_revision: str) -> dict[str, Any]:
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    dest = folder / stamp
    suffix = 2
    while dest.exists():
        dest = folder / f"{stamp}-{suffix}"
        suffix += 1
    dest.mkdir(parents=True)

    url = make_url(settings.database_url)
    kind = url.get_backend_name()
    if kind == "sqlite":
        _checkpoint_sqlite()
        db, extras = _sqlite_paths()
        if db is None or not db.is_file():
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError("SQLite のファイルが見つかりません。表はまだ更新していません。")
        shutil.copy2(db, dest / db.name)
        for extra in extras:
            shutil.copy2(extra, dest / extra.name)
        copied = dest / db.name
        if copied.stat().st_size == 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError("控えが 0 バイトでした。表はまだ更新していません。")
    else:
        _dump_postgres(dest)

    documents_bytes = _copy_documents(dest)
    total = folder_size(dest)
    if total <= 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("控えが空でした。表はまだ更新していません。")

    created = _now().isoformat()
    record = {
        "id": dest.name,
        "path": str(dest),
        "created_at": created,
        "from_revision": from_revision,
        "to_revision": to_revision,
        "bytes": total,
        "documents_bytes": documents_bytes,
        "kind": kind,
        "confirmed_at": None,
    }
    (dest / "manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    items = _read_index()
    items.append(record)
    _write_index(items)
    logger.info("表を更新する前の控えを取りました: %s (%s bytes)", dest, total)
    return record


def inventory_counts() -> dict[str, int] | None:
    from sqlalchemy import text

    from app.db import engine

    try:
        with engine.connect() as connection:
            devices = connection.execute(text("select count(*) from devices")).scalar()
            sites = connection.execute(text("select count(*) from sites")).scalar()
        return {"devices": int(devices or 0), "sites": int(sites or 0)}
    except Exception:
        return None


def schema_status() -> dict[str, Any]:
    from app.bootstrap import _schema_apply_disabled
    from app.db import engine
    from app.schema_alembic import current_revision, head_revision, is_empty, pending_steps

    if _schema_apply_disabled():
        return {
            "phase": "ready",
            "current_revision": None,
            "head_revision": "",
            "pending": [],
            "counts": None,
            "unconfirmed": [],
        }

    head = head_revision()

    current = current_revision(engine)
    pending = pending_steps(engine)
    unconfirmed = unconfirmed_backups()
    empty = is_empty(engine)

    if empty:
        phase = "ready"
    elif pending:
        phase = "needs_apply"
    elif unconfirmed:
        phase = "needs_confirm"
    else:
        phase = "ready"

    return {
        "phase": phase,
        "current_revision": current,
        "head_revision": head,
        "pending": pending,
        "counts": inventory_counts(),
        "unconfirmed": unconfirmed,
    }


def needs_apply() -> bool:
    return schema_status()["phase"] == "needs_apply"


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_lock() -> Path:
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / LOCK_NAME
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        stale = False
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
            stale = not _pid_is_running(pid)
        except ValueError:
            stale = True
        if not stale:
            raise RuntimeError(
                "いま表を更新している最中です。終わるまで待ってから画面を読み直してください。"
            ) from None
        logger.warning("中断された更新のロックを外します: %s", lock)
        lock.unlink(missing_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def apply_with_backup() -> dict[str, Any]:
    """控えを取ってから表を更新する。控えが取れなければ更新しない。"""
    from app.db import engine
    from app.schema_alembic import apply_schema, current_revision, head_revision, pending_steps

    lock = _acquire_lock()
    try:
        if not pending_steps(engine):
            return schema_status()

        from_revision = current_revision(engine)
        to_revision = head_revision()
        _create_backup_folder(from_revision, to_revision)
        apply_schema()
        engine.dispose()
        return schema_status()
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            logger.warning("更新用のロックファイルを消せませんでした: %s", lock)


def confirm_backup(backup_id: str | None = None) -> dict[str, Any]:
    """問題ないと印を付ける。控えのファイルは消さない。"""
    items = _read_index()
    targets = [item for item in items if not item.get("confirmed_at")]
    if backup_id:
        targets = [item for item in targets if item.get("id") == backup_id]
    if not targets:
        raise RuntimeError("確認待ちの控えがありません。")

    confirmed = _now().isoformat()
    ids = {item["id"] for item in targets}
    for item in items:
        if item.get("id") in ids:
            item["confirmed_at"] = confirmed
            manifest = Path(item["path"]) / "manifest.json"
            if manifest.is_file():
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["confirmed_at"] = confirmed
                manifest.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    _write_index(items)
    return schema_status()
