"""裏で走らせている仕事の口。

受け付けた仕事の様子を聞く・やめる、そして一覧で見る。仕事を始める口は、
それぞれの機能の側（取り込み、Config の下書き）に置いてある。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.jobs import JobRead, job_read
from app.services import jobs

router = APIRouter(tags=["jobs"])


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(limit: int = Query(default=20, ge=1, le=50)) -> list[JobRead]:
    """裏で走らせた仕事を、新しい順に返す。

    覚えているのは動いているあいだだけで、起動し直すと消える。
    """
    return [job_read(job) for job in jobs.recent(limit)]


@router.get("/jobs/{job_id}", response_model=JobRead)
def read_job(job_id: str) -> JobRead:
    """1件の様子。終わっていれば中身も返す。"""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その処理は残っていません")
    return job_read(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: str) -> JobRead:
    """やめる。

    区切りごとに様子を書き込む仕事（取り込み）は、そこで自分から降りる。
    降りられない仕事（モデルの生成）は最後まで走るが、結果は捨てる。
    """
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その処理は残っていません")
    return job_read(job)
