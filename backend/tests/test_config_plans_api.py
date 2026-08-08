"""現行フル / 将来フル / 差分 の3つが、それぞれの使い道どおりに出せること。"""

from pathlib import Path

from tests.conftest import API

FIXTURE = Path(__file__).parent / "fixtures" / "core-sw-01.txt"


def upload_config(client) -> None:
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", FIXTURE.read_bytes(), "text/plain"))],
    )


def make_device(client, device_payload, **overrides):
    payload = device_payload(hostname="core-sw-01", vendor="Cisco", **overrides)
    return client.post(f"{API}/devices", json=payload).json()["id"]


def make_plan(client, device_id, **fields):
    payload = {"device_id": device_id, "name": "更改後の構成"}
    payload.update(fields)
    return client.post(f"{API}/config-plans", json=payload)


# ---------------------------------------------------------------------------
# 3つの成果物
# ---------------------------------------------------------------------------


def test_plan_seeds_from_the_current_config(client, device_payload):
    """まっさらから書くことはまず無いので、既定は現行のコピー。"""
    device_id = make_device(client, device_payload)
    upload_config(client)

    plan = make_plan(client, device_id).json()
    assert "interface GigabitEthernet1/0/1" in plan["body"]


def test_outputs_return_current_future_and_diff(client, device_payload):
    device_id = make_device(client, device_payload)
    upload_config(client)

    plan = make_plan(client, device_id).json()
    client.patch(
        f"{API}/config-plans/{plan['id']}",
        json={"body": plan["body"].replace("->edge-rt-01", "->edge-rt-02")},
    )

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()

    # 現行フルは復旧用にそのまま
    assert "interface GigabitEthernet1/0/1" in out["current_full"]
    assert "->edge-rt-01" in out["current_full"]
    # 将来フルは更改後の機器にそのまま
    assert "->edge-rt-02" in out["future_full"]
    # 差分は既存機器に当てる分だけ
    assert out["diff"]["verified"] is True
    assert "description ->edge-rt-02" in out["diff"]["script"]
    assert "interface GigabitEthernet1/0/1" in out["diff"]["script"]


def test_diff_converges_to_the_future_full(client, device_payload):
    """差分を当てた結果が将来フルと同じになる、が守られていること。"""
    from app.services.configdiff.dialects import for_vendor
    from app.services.configdiff.engine import apply_script, equivalent, normalize
    from app.services.configdiff.tree import parse

    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()

    future = plan["body"].replace("->edge-rt-01", "->edge-rt-02") + "ntp server 10.0.0.5\n"
    client.patch(f"{API}/config-plans/{plan['id']}", json={"body": future})

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    dialect = for_vendor(out["vendor"])

    applied = apply_script(
        normalize(parse(out["current_full"], dialect), dialect), out["diff"]["script"], dialect
    )
    expected = normalize(parse(out["future_full"], dialect), dialect)
    assert equivalent(applied, expected, dialect, ())


def test_unchanged_plan_has_an_empty_diff(client, device_payload):
    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert out["diff"]["script"] == ""
    assert out["diff"]["verified"] is True


# ---------------------------------------------------------------------------
# ダウンロード
# ---------------------------------------------------------------------------


def test_each_output_can_be_downloaded(client, device_payload):
    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()
    client.patch(
        f"{API}/config-plans/{plan['id']}", json={"body": plan["body"] + "ntp server 10.0.0.5\n"}
    )

    for kind in ("current", "future", "diff", "rollback"):
        res = client.get(f"{API}/config-plans/{plan['id']}/download/{kind}")
        assert res.status_code == 200, kind
        assert "attachment" in res.headers["content-disposition"]

    assert (
        "core-sw-01-current.txt"
        in client.get(f"{API}/config-plans/{plan['id']}/download/current").headers[
            "content-disposition"
        ]
    )


def test_unverified_diff_cannot_be_downloaded(client, device_payload, monkeypatch):
    """収束していない差分をファイルで渡してしまうのが一番危ない。"""
    from app.services.configdiff import engine

    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()
    client.patch(
        f"{API}/config-plans/{plan['id']}", json={"body": plan["body"] + "ntp server 10.0.0.5\n"}
    )

    monkeypatch.setattr(engine, "apply_script", lambda base, script, dialect: base)

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert out["diff"]["verified"] is False
    assert out["diff"]["commands"] == []
    assert out["diff"]["mismatches"]

    res = client.get(f"{API}/config-plans/{plan['id']}/download/diff")
    assert res.status_code == 422

    # 現行フルと将来フルは差分と無関係なので、落とせるままであること
    assert client.get(f"{API}/config-plans/{plan['id']}/download/current").status_code == 200
    assert client.get(f"{API}/config-plans/{plan['id']}/download/future").status_code == 200


# ---------------------------------------------------------------------------
# 基準のずれ
# ---------------------------------------------------------------------------


def test_stale_base_is_reported(client, device_payload):
    """基準にした現行より新しい config が来たら、差分はずれる。"""
    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()

    # 後からもう1世代取り込む
    client.post(
        f"{API}/configs/upload",
        files=[
            (
                "files",
                (
                    "core-sw-01.txt",
                    FIXTURE.read_bytes() + b"\nntp server 10.0.0.9\n",
                    "text/plain",
                ),
            )
        ],
    )

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert out["base_is_stale"] is True
    assert any("新しい" in n for n in out["notes"])


def test_device_without_config_is_told_so(client, device_payload):
    device_id = make_device(client, device_payload)
    plan = make_plan(client, device_id).json()

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert out["current_full"] == ""
    assert any("取り込まれていない" in n for n in out["notes"])


# ---------------------------------------------------------------------------
# 注意喚起
# ---------------------------------------------------------------------------


def test_dangerous_diff_is_flagged(client, device_payload):
    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()

    # インターフェースをまるごと消す将来構成
    trimmed = "\n".join(
        line
        for line in plan["body"].splitlines()
        if not line.startswith("interface GigabitEthernet1/0/1")
    )
    client.patch(f"{API}/config-plans/{plan['id']}", json={"body": trimmed + "\n"})

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert out["diff"]["risks"]


def test_rollback_is_offered(client, device_payload):
    device_id = make_device(client, device_payload)
    upload_config(client)
    plan = make_plan(client, device_id).json()
    client.patch(
        f"{API}/config-plans/{plan['id']}", json={"body": plan["body"] + "ntp server 10.0.0.5\n"}
    )

    out = client.get(f"{API}/config-plans/{plan['id']}/outputs").json()
    assert "no ntp server 10.0.0.5" in out["diff"]["rollback_script"]


# ---------------------------------------------------------------------------
# 管理
# ---------------------------------------------------------------------------


def test_editing_the_body_bumps_the_revision(client, device_payload):
    device_id = make_device(client, device_payload)
    plan = make_plan(client, device_id, seed_from="blank", body="hostname a\n").json()
    assert plan["revision"] == 1

    updated = client.patch(f"{API}/config-plans/{plan['id']}", json={"body": "hostname b\n"}).json()
    assert updated["revision"] == 2

    # 本文が変わらない更新では上げない
    same = client.patch(f"{API}/config-plans/{plan['id']}", json={"name": "改称"}).json()
    assert same["revision"] == 2


def test_plan_crud(client, device_payload):
    device_id = make_device(client, device_payload)
    plan = make_plan(client, device_id, seed_from="blank").json()

    assert len(client.get(f"{API}/devices/{device_id}/config-plans").json()) == 1
    assert client.delete(f"{API}/config-plans/{plan['id']}").status_code == 204
    assert client.get(f"{API}/config-plans").json() == []


def test_unknown_device_is_rejected(client):
    res = client.post(f"{API}/config-plans", json={"device_id": 999, "name": "x"})
    assert res.status_code == 422


def test_preview_compares_two_configs_without_saving(client):
    res = client.post(
        f"{API}/config-plans/preview",
        json={
            "current": "interface Gi1/0/1\n description old\n",
            "target": "interface Gi1/0/1\n description new\n",
        },
    ).json()

    assert res["verified"] is True
    assert "description new" in res["script"]
    assert client.get(f"{API}/config-plans").json() == []
