"""取り込みを裏で走らせる。

16万行の台帳は数十秒かかる。そのあいだ画面を止めると、押しても反応が
無いように見え、ブラウザ側で先に切れることもあった。受け付けたらすぐ
番号を返し、進み具合を聞ける形にする。
"""

import json
import time

from app.services import jobs
from tests.conftest import API

CSV_HEADER = "ホスト名,メーカー,型番\n"
CSV_ROWS = "core-sw-01,Cisco,C9500-40X\nedge-rt-01,Juniper,MX204\n"


def _wait(client, job_id: str, timeout: float = 5.0) -> dict:
    limit = time.time() + timeout
    while time.time() < limit:
        body = client.get(f"{API}/jobs/{job_id}").json()
        if body["status"] in {"done", "error", "cancelled"}:
            return body
        time.sleep(0.02)
    raise AssertionError("裏の取り込みが終わらない")


def _start(client, body: str = CSV_HEADER + CSV_ROWS, name: str = "daichou.csv") -> dict:
    res = client.post(
        f"{API}/import/devices/async",
        files={"file": (name, body.encode("utf-8"), "text/csv")},
        data={"key_field": "hostname"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_it_answers_before_the_import_finishes(client):
    job = _start(client)
    assert job["status"] in {"pending", "running"}
    assert job["kind"] == "device-import"
    assert job["kind_label"] == "機器台帳の取り込み"
    assert job["label"] == "daichou.csv"


def test_the_rows_land_in_the_ledger(client):
    job = _start(client)
    done = _wait(client, job["id"])
    assert done["status"] == "done"
    assert done["device_import"]["committed"] is True
    assert done["device_import"]["created"] == 2
    assert len(client.get(f"{API}/devices").json()) == 2


def test_progress_is_visible_while_it_runs(client):
    job = _start(client)
    done = _wait(client, job["id"])
    assert done["done"] == done["total"] == 2
    assert done["percent"] == 100


def test_a_broken_file_is_kept_as_a_message(client):
    job = _start(client, body="%PDF-1.4", name="shiryou.pdf")
    done = _wait(client, job["id"])
    assert done["status"] == "error"
    assert done["error"]


def test_a_bad_mapping_is_refused_on_the_spot(client):
    """裏に回してから列名の誤りが分かっても、待ったぶんが無駄になる。"""
    res = client.post(
        f"{API}/import/devices/async",
        files={"file": ("daichou.csv", CSV_HEADER.encode("utf-8"), "text/csv")},
        data={"mapping": json.dumps({"ホスト名": "そんな項目"})},
    )
    assert res.status_code == 422


def test_links_can_run_in_the_background(client, device_payload):
    for hostname in ("core-sw-01", "edge-sw-01"):
        client.post(f"{API}/devices", json=device_payload(hostname=hostname))
    table = "機器A,ポートA,機器B,ポートB\ncore-sw-01,Gi1/0/1,edge-sw-01,Gi0/1\n"
    res = client.post(
        f"{API}/import/links/async",
        files={"file": ("kessen.csv", table.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200, res.text
    done = _wait(client, res.json()["id"])
    assert done["status"] == "done"
    assert done["link_import"]["linked"] == 1


def test_the_dashboard_lists_what_ran(client):
    _start(client)
    _start(client, name="tsuika.csv")
    listed = client.get(f"{API}/jobs").json()
    assert [j["label"] for j in listed[:2]] == ["tsuika.csv", "daichou.csv"]
    assert all(j["kind_label"] == "機器台帳の取り込み" for j in listed[:2])


def test_giving_up_stops_a_running_import(client):
    """やめる印を付けたら、区切りで自分から降りる。"""

    def slow(job) -> None:
        for i in range(1000):
            job.advance(i, 1000)
            time.sleep(0.005)

    job = jobs.start("device-import", slow)
    time.sleep(0.05)
    jobs.cancel(job.id)
    limit = time.time() + 5
    while time.time() < limit and job.status not in {"done", "error", "cancelled"}:
        time.sleep(0.02)
    assert job.status == "cancelled"


def test_an_unknown_job_is_a_404(client):
    assert client.get(f"{API}/jobs/nosuchjob").status_code == 404
