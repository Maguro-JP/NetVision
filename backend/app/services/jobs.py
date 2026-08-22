"""時間のかかる処理を、裏で走らせる。

手元のモデルに Config の下書きを頼むと、CPU では数十秒かかる。そのあいだ
画面が固まったように見え、押しても反応が無いと思われる。実際にそう見えていた。

そこで、受け付けたらすぐ番号を返し、処理は裏で進める。画面はその番号で
様子を聞く。

作りはできるだけ小さくしてある。

- 1台完結で動かす前提なので、覚えておく場所はプロセスの中だけ。DB に入れない
  （表の更新を通さずに足せる）
- 起動し直すと消える。消えて困るもの（取り込みや書き出し）はここに載せない。
  ここに載せるのは「もう一度頼めば済むもの」だけ
- 数が増え続けないよう、古いものから落とす
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: 覚えておく数。これを超えたら古いものから落とす
_KEEP = 50

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str
    status: str = PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None

    @property
    def seconds(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


_jobs: OrderedDict[str, Job] = OrderedDict()
_lock = threading.Lock()


def start(kind: str, work: Callable[[], Any]) -> Job:
    """受け付けて、すぐ番号を返す。処理は裏で進む。"""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    with _lock:
        _jobs[job.id] = job
        while len(_jobs) > _KEEP:
            _jobs.popitem(last=False)

    def run() -> None:
        job.status = RUNNING
        try:
            job.result = work()
            job.status = DONE
        except Exception as error:  # noqa: BLE001  裏で落ちても道具は止めない
            logger.warning("裏の処理（%s）が落ちました: %s", kind, error)
            job.error = str(error)
            job.status = ERROR
        finally:
            job.finished_at = datetime.now(UTC)

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def cancel(job_id: str) -> Job | None:
    """やめる。

    走っている処理そのものは止められない（モデルの生成は途中で切れない）。
    結果を捨てて、画面を待たせないための印を付ける。
    """
    job = get(job_id)
    if job is None:
        return None
    if job.status in {PENDING, RUNNING}:
        job.status = CANCELLED
        job.finished_at = datetime.now(UTC)
    return job
