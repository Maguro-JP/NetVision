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
