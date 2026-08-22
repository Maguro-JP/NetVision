"""時間のかかる処理を、裏で走らせる。

手元のモデルに Config の下書きを頼むと、CPU では数十秒かかる。そのあいだ
画面が固まったように見え、押しても反応が無いと思われる。実際にそう見えていた。

そこで、受け付けたらすぐ番号を返し、処理は裏で進める。画面はその番号で
様子を聞く。

作りはできるだけ小さくしてある。

- 1台完結で動かす前提なので、覚えておく場所はプロセスの中だけ。DB に入れない
  （表の更新を通さずに足せる）
- 起動し直すと消える。載せるのは「もう一度頼めば済むもの」だけにする。
  取り込みもここに載せているが、消えるのは経過の記録だけで、DB に入った
  ぶんは残る。途中で落ちたときは、同じファイルをもう一度渡せば済む
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


class Cancelled(Exception):
    """やめる指示が出ていたので、途中で降りた。"""


PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str
    #: 画面に出す説明。「機器台帳の取り込み: 拠点一覧.csv」など
    label: str = ""
    status: str = PENDING
    #: 進み具合。total が分からないうちは None
    done: int = 0
    total: int | None = None
    note: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None

    @property
    def percent(self) -> int | None:
        """何割まで来たか。総数が分からないうちは None。"""
        if not self.total:
            return None
        return max(0, min(100, round(self.done * 100 / self.total)))

    def advance(self, done: int, total: int | None = None, note: str | None = None) -> None:
        """進み具合を書き換える。やめる指示が出ていたら、そこで降りる。

        走っている処理そのものを外から止める術は無い。区切りごとにここを
        通ってもらい、自分から降りてもらう。
        """
        self.done = done
        if total is not None:
            self.total = total
        if note is not None:
            self.note = note
        if self.status == CANCELLED:
            raise Cancelled

    @property
    def seconds(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


_jobs: OrderedDict[str, Job] = OrderedDict()
_lock = threading.Lock()


def start(kind: str, work: Callable[[Job], Any], *, label: str = "") -> Job:
    """受け付けて、すぐ番号を返す。処理は裏で進む。

    `work` には仕事そのものを渡す。進み具合を見せたい処理は、区切りごとに
    `job.advance(...)` を呼ぶ。
    """
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
    with _lock:
        _jobs[job.id] = job
        while len(_jobs) > _KEEP:
            _jobs.popitem(last=False)

    def run() -> None:
        if job.status == CANCELLED:  # 始まる前にやめられた
            return
        job.status = RUNNING
        try:
            job.result = work(job)
            job.status = DONE
        except Cancelled:
            job.status = CANCELLED
        except Exception as error:  # noqa: BLE001  裏で落ちても道具は止めない
            logger.warning("裏の処理（%s）が落ちました: %s", kind, error)
            job.error = str(error)
            job.status = ERROR
        finally:
            job.finished_at = datetime.now(UTC)

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return job


def recent(limit: int = 20) -> list[Job]:
    """新しい順に並べて返す。ダッシュボードで一覧にする。"""
    with _lock:
        return list(reversed(list(_jobs.values())))[:limit]


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def cancel(job_id: str) -> Job | None:
    """やめる。

    印を付けるだけで、走っている処理をその場で止めはしない。区切りごとに
    `job.advance(...)` を通る処理は、そこで自分から降りる。通らない処理
    （モデルの生成など）は最後まで走るが、結果は捨てる。
    """
    job = get(job_id)
    if job is None:
        return None
    if job.status in {PENDING, RUNNING}:
        job.status = CANCELLED
        job.finished_at = datetime.now(UTC)
    return job
