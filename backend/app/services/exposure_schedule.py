"""該当状況の洗い直しを、決めた間隔で裏で回す。

洗い直しを人が押すのを忘れると「未対応ゼロ」が古いまま信用されてしまう。
ここでは決めた時間おきに洗い直し、前回との差（未対応が増えたか）を
覚えておく。画面はこれを見て「前回から N 件増えました」と出す。

外へ通知（メール等）は送らない。送り先の設定や認証が絡むので、
それは認証が入ってから決める。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.services import appsettings
from app.services import exposure as service

logger = logging.getLogger(__name__)

SETTING_KEY = "exposure_sync_hours"
#: 最後に洗い直した記録の置き場所。動かし直しても画面から消えないようにする
LAST_RUN_KEY = "exposure_sync_last"
DEFAULT_HOURS = 24
MAX_HOURS = 24 * 30


@dataclass
class LastRun:
    ran_at: datetime
    trigger: str  # "manual" / "scheduled"
    unhandled_before: int
    unhandled_after: int
    messages: list[str] = field(default_factory=list)

    @property
    def grew_by(self) -> int:
        return max(0, self.unhandled_after - self.unhandled_before)


_last: LastRun | None = None
_lock = threading.Lock()
_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None


def interval_hours() -> int:
    """何時間おきに回すか。0 は止める。"""
    value = appsettings.load().get(SETTING_KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_HOURS
    return min(value, MAX_HOURS)


def run(db: Session, *, trigger: str = "manual") -> service.SyncResult:
    """洗い直して、前回との差を覚える。未対応が増えたら文で残す。"""
    global _last
    before = service.summary(db).unhandled
    result = service.sync(db)
    after = service.summary(db).unhandled
    if after > before:
        result.messages.append(f"未対応が {before} 件から {after} 件に増えました")
    record = LastRun(
        ran_at=datetime.now(UTC),
        trigger=trigger,
        unhandled_before=before,
        unhandled_after=after,
        messages=list(result.messages),
    )
    with _lock:
        _last = record
    _remember(record)
    return result


def _remember(record: LastRun) -> None:
    """記録をファイルにも残す。動かし直すと消えるのでは、いつのものか分からない。"""
    try:
        appsettings.set_value(
            LAST_RUN_KEY,
            {
                "ran_at": record.ran_at.isoformat(),
                "trigger": record.trigger,
                "unhandled_before": record.unhandled_before,
                "unhandled_after": record.unhandled_after,
                "messages": record.messages,
            },
        )
    except RuntimeError:
        # 書けなくても、洗い直しそのものは終わっている
        logger.warning("洗い直しの記録を残せませんでした")


def _restore() -> LastRun | None:
    saved = appsettings.load().get(LAST_RUN_KEY)
    if not isinstance(saved, dict):
        return None
    try:
        ran_at = datetime.fromisoformat(str(saved.get("ran_at")))
    except ValueError:
        return None
    if ran_at.tzinfo is None:
        ran_at = ran_at.replace(tzinfo=UTC)
    messages = saved.get("messages")
    return LastRun(
        ran_at=ran_at,
        trigger=str(saved.get("trigger") or "manual"),
        unhandled_before=int(saved.get("unhandled_before") or 0),
        unhandled_after=int(saved.get("unhandled_after") or 0),
        messages=[str(m) for m in messages] if isinstance(messages, list) else [],
    )


def last_run() -> LastRun | None:
    with _lock:
        if _last is not None:
            return _last
    return _restore()


def next_run_at() -> datetime | None:
    hours = interval_hours()
    if hours == 0 or _thread is None or not _thread.is_alive():
        return None
    found = last_run()
    base = max(found.ran_at, _started_at) if found is not None else _started_at
    return base + timedelta(hours=hours)


_started_at = datetime.now(UTC)


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
        from app.db import new_session

        db = new_session()
        try:
            result = run(db, trigger="scheduled")
            logger.info("該当状況を洗い直しました: %s", " / ".join(result.messages))
        except Exception:  # noqa: BLE001  裏の処理で道具を止めない
            logger.exception("該当状況の洗い直しに失敗しました")
        finally:
            db.close()


def start() -> None:
    """起動時に呼ぶ。二重には起動しない。"""
    global _thread, _started_at
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _started_at = datetime.now(UTC)
    _thread = threading.Thread(target=_loop, name="exposure-sync", daemon=True)
    _thread.start()


def reschedule() -> None:
    """間隔を変えたときに呼ぶ。次の起動時刻を計算し直す。"""
    _wake.set()


def stop() -> None:
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout=2)
