"""控えを定期的に取り、本当に戻せる形になっているかまで見る。

「取れているつもりで戻せない」が一番困る。ここでは取るだけで終わらせず、
取った控えを開いて中身を数える。開けないものは、取れていないものとして扱う。

戻す操作そのものは持たない。データを上書きする作業なので、人が手順を見て
行う。手順は docs/OPERATIONS.md にある。ここは「どこに、いつのものがあり、
それが開けるか」までを引き受ける。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services import appsettings, schema_gate

logger = logging.getLogger(__name__)

INTERVAL_KEY = "backup_interval_hours"
KEEP_KEY = "backup_keep"
DEFAULT_HOURS = 24
DEFAULT_KEEP = 7
MAX_HOURS = 24 * 30
MAX_KEEP = 100


def _number(key: str, fallback: int, largest: int) -> int:
    value = appsettings.load().get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return fallback
    return min(value, largest)


def interval_hours() -> int:
    """何時間おきに取るか。0 は自動で取らない。"""
    return _number(INTERVAL_KEY, DEFAULT_HOURS, MAX_HOURS)


def keep() -> int:
    """何世代残すか。0 は消さない。"""
    return _number(KEEP_KEY, DEFAULT_KEEP, MAX_KEEP)


# ---------------------------------------------------------------------------
# 取ったものが本当に開けるか
# ---------------------------------------------------------------------------


@dataclass
class Verification:
    ok: bool
    message: str
    counts: dict[str, int] | None = None


def verify(folder: Path) -> Verification:
    """控えを開いて、壊れていないかと中身の件数を見る。

    SQLite は別のファイルとして開き直して整合性を見る。動いている DB の
    ほうを見ても、控えが壊れていることは分からない。
    """
    if not folder.is_dir():
        return Verification(False, f"控えのフォルダがありません（{folder}）")

    dump = folder / "database.dump"
    if dump.is_file():
        # Postgres の控え。中身を開くには pg_restore が要るので、
        # ここでは形と大きさだけを見る。開けるかどうかは戻すときに分かる
        if dump.stat().st_size <= 0:
            return Verification(False, "控えが 0 バイトです")
        return Verification(
            True,
            f"Postgres の控えがあります（{dump.stat().st_size} バイト）。"
            "中身は pg_restore で確認してください",
        )

    files = sorted(folder.glob("*.db")) + sorted(folder.glob("*.sqlite*"))
    if not files:
        return Verification(False, "控えの中に DB のファイルが見当たりません")

    path = files[0]
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return Verification(False, f"控えの DB を開けません: {error}")
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            return Verification(
                False, f"控えの DB が壊れています: {result[0] if result else '不明'}"
            )
        counts: dict[str, int] = {}
        for table in ("devices", "sites", "config_snapshots"):
            try:
                found = connection.execute(f"select count(*) from {table}").fetchone()  # noqa: S608
            except sqlite3.Error:
                continue
            counts[table] = int(found[0] if found else 0)
    except sqlite3.Error as error:
        return Verification(False, f"控えの DB を読めません: {error}")
    finally:
        connection.close()

    if not counts:
        return Verification(False, "控えの DB に台帳の表が入っていません")
    words = " / ".join(f"{name} {number} 件" for name, number in counts.items())
    return Verification(True, f"開いて中身を数えました（{words}）", counts)


# ---------------------------------------------------------------------------
# 取る・消す
# ---------------------------------------------------------------------------


def _record_verification(record: dict[str, Any], result: Verification) -> None:
    record["verified"] = result.ok
    record["verify_message"] = result.message
    if result.counts:
        record["counts"] = result.counts
    folder = Path(record["path"])
    manifest = folder / "manifest.json"
    if folder.is_dir():
        manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    items = schema_gate._read_index()
    for item in items:
        if item.get("id") == record.get("id"):
            item.update(record)
    schema_gate._write_index(items)


def run(reason: str = "定期") -> dict[str, Any]:
    """いま取って、開けるところまで確かめる。"""
    record = schema_gate.create_backup(reason)
    _record_verification(record, verify(Path(record["path"])))
    prune()
    return record


def listing() -> list[dict[str, Any]]:
    """新しい順。無くなっているフォルダは、その旨を添えて残す。"""
    items = list(schema_gate._read_index())
    for item in items:
        item["exists"] = Path(item.get("path", "")).is_dir()
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items


def prune() -> list[str]:
    """古いものから消す。表の更新前に取ったもの（戻す先）は消さない。"""
    limit = keep()
    if limit <= 0:
        return []
    items = [item for item in listing() if item.get("exists")]
    removable = [item for item in items if item.get("from_revision") is None]
    removed: list[str] = []
    for item in removable[limit:]:
        folder = Path(item["path"])
        shutil.rmtree(folder, ignore_errors=True)
        item["exists"] = False
        removed.append(item["id"])
    if removed:
        index = schema_gate._read_index()
        for entry in index:
            if entry.get("id") in removed:
                entry["removed"] = True
        schema_gate._write_index(index)
        logger.info("古い控えを %d 件片付けました", len(removed))
    return removed


def latest() -> dict[str, Any] | None:
    for item in listing():
        if item.get("exists"):
            return item
    return None


# ---------------------------------------------------------------------------
# 定期実行
# ---------------------------------------------------------------------------

_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None
_started_at = datetime.now(UTC)


def _last_time() -> datetime:
    found = latest()
    if found is None:
        return _started_at
    try:
        stamp = datetime.fromisoformat(str(found.get("created_at")))
    except ValueError:
        return _started_at
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def next_run_at() -> datetime | None:
    hours = interval_hours()
    if hours == 0 or _thread is None or not _thread.is_alive():
        return None
    return max(_last_time(), _started_at) + timedelta(hours=hours)


def _loop() -> None:
    while not _stop.is_set():
        due = next_run_at()
        if due is None:
            _wake.wait()
            _wake.clear()
            continue
        remaining = (due - datetime.now(UTC)).total_seconds()
        if remaining > 0:
            _wake.wait(remaining)
            if _wake.is_set():
                _wake.clear()
                continue
            if _stop.is_set():
                break
        try:
            record = run("定期")
            logger.info("控えを取りました: %s（%s）", record["path"], record.get("verify_message"))
        except Exception:  # noqa: BLE001  裏の処理で道具を止めない
            logger.exception("控えを取れませんでした")
            # 取れなかったときに同じ失敗を繰り返さないよう、次の周回まで待つ
            _wake.wait(min(3600, max(60, interval_hours() * 60)))
            _wake.clear()


def start() -> None:
    global _thread, _started_at
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _started_at = datetime.now(UTC)
    _thread = threading.Thread(target=_loop, name="backup", daemon=True)
    _thread.start()


def reschedule() -> None:
    _wake.set()


def stop() -> None:
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout=2)


def running() -> bool:
    return _thread is not None and _thread.is_alive()
