"""裏で走らせている仕事の見え方。

画面（ダッシュボード）に並べるので、何の仕事か・どこまで進んだか・
終わったならその結果、をひとまとめにして返す。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.config_plans import ConfigDraftRead
from app.schemas.imports import ImportResult, LinkImportResultRead

#: 仕事の種類ごとの、人が読む呼び名
KIND_LABELS = {
    "config-draft": "Config の下書き",
    "device-import": "機器台帳の取り込み",
    "link-import": "結線表の取り込み",
}


class JobRead(BaseModel):
    """裏の処理の様子。"""

    id: str
    kind: str
    kind_label: str = Field(default="", description="種類の呼び名（画面表示用）")
    label: str = Field(default="", description="何に対する仕事か（ファイル名など）")
    status: str
    seconds: float
    started_at: datetime | None = None
    #: 進み具合。総数が分からないうちは total も percent も空
    done: int = 0
    total: int | None = None
    percent: int | None = None
    note: str | None = None
    error: str | None = None
    #: 終わったときの中身。仕事の種類によってどれか1つが入る
    draft: ConfigDraftRead | None = None
    device_import: ImportResult | None = None
    link_import: LinkImportResultRead | None = None


def job_read(job) -> JobRead:  # noqa: ANN001  jobs.Job（取り込み側の循環を避ける）
    result = job.result
    return JobRead(
        id=job.id,
        kind=job.kind,
        kind_label=KIND_LABELS.get(job.kind, job.kind),
        label=job.label,
        status=job.status,
        seconds=round(job.seconds, 1),
        started_at=job.started_at,
        done=job.done,
        total=job.total,
        percent=job.percent,
        note=job.note,
        error=job.error,
        draft=result if isinstance(result, ConfigDraftRead) else None,
        device_import=result if isinstance(result, ImportResult) else None,
        link_import=result if isinstance(result, LinkImportResultRead) else None,
    )
