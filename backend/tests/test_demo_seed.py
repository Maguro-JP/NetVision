"""デモで見せる画面に、中身が入っていること。

台帳だけ入れても、点検・L3・計画の画面は空のままになる。この道具の値打ちは
そこから先にあるので、見せる段で空にならないことを確かめる。
"""

import pathlib

from tests.conftest import API

DEMO = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "demo_configs"


def _load(client):
    """seed と同じ Config を、画面と同じ道で入れる。"""
    out = []
    for path in sorted(DEMO.glob("*.txt")):
        res = client.post(
            f"{API}/configs/upload",
            files={"files": (path.name, path.read_bytes(), "text/plain")},
            data={"register_unknown": "true"},
        )
        assert res.status_code == 200, res.text
        out.extend(res.json())
    return out


def test_the_demo_configs_are_all_read():
    """読めない Config をデモに置かない。"""
    from app.services import configparse

    assert list(DEMO.glob("*.txt")), "デモ用の Config がありません"
    for path in sorted(DEMO.glob("*.txt")):
        parsed = configparse.parse(path.read_text(encoding="utf-8"))
        assert parsed.hostname == path.stem, f"{path.name} の名前を読めません"
        assert parsed.vendor != "unknown", f"{path.name} のベンダーを判別できません"


def test_every_device_gets_ports_and_vlans(client):
    for item in _load(client):
        assert item["device_hostname"]
        assert item["interface_count"] > 0, item
        assert item["neighbor_count"] > 0, item


def test_the_check_screen_has_something_to_show(client):
    _load(client)
    devices = client.get(f"{API}/devices").json()
    found = 0
    for device in devices:
        res = client.get(f"{API}/devices/{device['id']}/l3")
        assert res.status_code == 200, res.text
        found += len(res.json()["findings"])
    assert found > 0, "どの機器にも指摘が出ません。デモで見せるものがありません"


def test_the_topology_gets_its_links_from_the_configs(client):
    _load(client)
    body = client.get(f"{API}/topology").json()
    assert len(body["nodes"]) >= 4
    assert body["edges"], "線が1本も出ません"


def test_a_config_plan_can_be_made_for_a_demo_device(client):
    _load(client)
    device = next(
        item for item in client.get(f"{API}/devices").json() if item["hostname"] == "core-sw-01"
    )
    res = client.post(
        f"{API}/config-plans",
        json={"device_id": device["id"], "name": "デモ", "seed_from": "current"},
    )
    assert res.status_code in (200, 201), res.text
    outputs = client.get(f"{API}/config-plans/{res.json()['id']}/outputs")
    assert outputs.status_code == 200, outputs.text
    assert outputs.json()["current_full"].startswith("!")


# ---------------------------------------------------------------------------
# 最初の画面の数え方
# ---------------------------------------------------------------------------


def test_every_device_is_counted_in_each_breakdown(client, device_payload):
    """内訳は機器を数える。1台でも欠けると、最初の画面の数字が信用できない。"""
    from datetime import date, timedelta

    from app.models import MaintenanceContract
    from app.services import device_query
    from tests.conftest import API as _API

    ids = []
    for index in range(4):
        res = client.post(f"{_API}/devices", json=device_payload(hostname=f"sw-{index:02d}"))
        ids.append(res.json()["id"])

    # 契約があるのは一部だけ。実際の台帳もそうなっている
    from app.db import get_db
    from app.main import app as fastapi_app

    db = fastapi_app.dependency_overrides[get_db]()
    db.add(
        MaintenanceContract(
            device_id=ids[0],
            provider="デモ",
            start_date=date.today() - timedelta(days=400),
            end_date=date.today() + timedelta(days=100),
        )
    )
    db.commit()

    for counts in (
        device_query.count_by_maintenance_severity(db),
        device_query.count_by_eol_severity(db),
        device_query.count_by_overall_severity(db),
    ):
        assert sum(counts.values()) == len(ids), counts

    maintenance = device_query.count_by_maintenance_severity(db)
    assert maintenance["unknown"] == len(ids) - 1, maintenance


def test_the_seed_counts_what_went_in_not_what_it_meant_to_put_in() -> None:
    """投入完了の数は、入れたつもりの数ではなく入ったあとの数であること。

    Config の取り込みでも結線とポートが増える。足し算で出していると、
    画面に出る数と食い違ったまま、その数を信じて話が進む。
    """
    from scripts import seed

    source = pathlib.Path(seed.__file__).read_text(encoding="utf-8")
    printed = source[source.index("投入完了") : source.index("該当{")]
    assert "len(wiring)" not in printed
    assert "_count(db, Link)" in printed
    assert "_count(db, Interface)" in printed


def test_the_planted_wiring_problems_are_actually_found(client):
    """仕込んだ問題が、実際に見つかること。

    「見つけられる」を見せる材料なので、仕込んだつもりで見つからないと
    デモの筋が通らない。資料に書いてあるのに出ない、が起きる。
    """
    _load(client)
    response = client.get(f"{API}/topology/link-check")
    assert response.status_code == 200, response.text
    titles = [item["title"] for item in response.json()["findings"]]
    assert [title for title in titles if "通す VLAN が両端でそろっていません" in title]
    assert [title for title in titles if "タグ無しで渡す VLAN が両端で違います" in title]
