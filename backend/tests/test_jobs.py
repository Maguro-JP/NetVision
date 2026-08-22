"""時間のかかる処理を裏で走らせる。

手元のモデルに下書きを頼むと CPU では数十秒かかる。そのあいだ画面が固まった
ように見え、押しても反応が無いと思われていた。受け付けたらすぐ番号を返し、
様子を聞ける形にする。
"""

import time

from app.services import jobs

API = "/api/v1"


def _wait(job_id: str, timeout: float = 5.0):
    """終わるまで待つ（試験の中だけ）。"""
    limit = time.time() + timeout
    while time.time() < limit:
        job = jobs.get(job_id)
        if job and job.status in {jobs.DONE, jobs.ERROR, jobs.CANCELLED}:
            return job
        time.sleep(0.02)
    raise AssertionError("裏の処理が終わらない")


def test_it_returns_at_once_and_finishes_later():
    """受け付けた時点で番号が返り、処理は裏で進む。"""
    job = jobs.start("test", lambda: (time.sleep(0.1), "できました")[1])
    assert job.status in {jobs.PENDING, jobs.RUNNING}

    done = _wait(job.id)
    assert done.status == jobs.DONE
    assert done.result == "できました"
    assert done.seconds >= 0.1


def test_a_failure_is_kept_as_a_message():
    """裏で落ちても道具は止めず、理由を残す。"""

    def broken():
        raise RuntimeError("読めませんでした")

    job = jobs.start("test", broken)
    done = _wait(job.id)
    assert done.status == jobs.ERROR
    assert "読めませんでした" in (done.error or "")


def test_waiting_can_be_given_up():
    """待つのをやめられること。生成そのものは切れないので、結果を捨てる。"""
    job = jobs.start("test", lambda: (time.sleep(1), "遅い")[1])
    cancelled = jobs.cancel(job.id)
    assert cancelled is not None
    assert cancelled.status == jobs.CANCELLED


def test_the_draft_can_be_asked_for_in_the_background(client):
    """画面からは、番号を受け取って様子を聞く形で使える。"""
    device = client.post(f"{API}/devices", json={"hostname": "qx-01", "vendor": "NEC"}).json()

    accepted = client.post(
        f"{API}/devices/{device['id']}/config-draft/async", json={"request": "vlan"}
    )
    assert accepted.status_code == 200
    job_id = accepted.json()["id"]

    _wait(job_id)
    body = client.get(f"{API}/jobs/{job_id}").json()
    assert body["status"] == "done"
    assert body["draft"] is not None
    assert body["draft"]["device_id"] == device["id"]


def test_an_unknown_job_is_reported(client):
    assert client.get(f"{API}/jobs/nosuchjob").status_code == 404
