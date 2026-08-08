"""対処漏れが起きないこと。

機器を1台ずつ開いて確認する運用は必ず漏れるので、
「漏れる経路」を1つずつ塞げているかを確かめる。
"""

import json

from tests.conftest import API

ADVISORY = [
    {
        "cve_id": "CVE-2026-0001",
        "title": "IOS-XE Web UI の認証回避",
        "cvss_score": 9.8,
        "cvss_severity": "CRITICAL",
        "known_exploited": True,
        "remediation_due": "2026-01-31",
        "affects": [
            {
                "vendor": "Cisco",
                "model": "C9500*",
                "os_name": "IOS-XE",
                "version_start_including": "17.9.0",
                "version_end_including": "17.9.5",
                "fixed_version": "17.9.6",
            }
        ],
    }
]


def import_advisory(client, payload=None):
    body = json.dumps(payload or ADVISORY).encode()
    return client.post(
        f"{API}/reference/vulnerabilities/import",
        files={"file": ("advisory.json", body, "application/json")},
    )


def add_device(client, device_payload, **overrides):
    fields = {
        "vendor": "Cisco",
        "model": "C9500-40X",
        "os_name": "IOS-XE",
        "os_version": "17.09.04a",
    }
    fields.update(overrides)
    return client.post(f"{API}/devices", json=device_payload(**fields)).json()["id"]


def sync(client):
    return client.post(f"{API}/exposures/sync").json()


def only(client, **params):
    return client.get(f"{API}/exposures", params=params).json()


# ---------------------------------------------------------------------------
# 漏れ経路 1: 全体が見えない
# ---------------------------------------------------------------------------


def test_every_device_and_cve_pair_is_tracked(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    add_device(client, device_payload, hostname="sw-2", serial_number="X2")

    result = sync(client)
    assert result["created"] == 2

    rows = only(client)
    assert {r["hostname"] for r in rows} == {"sw-1", "sw-2"}
    assert all(r["status"] == "open" for r in rows)


def test_summary_shows_what_still_needs_action(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)

    summary = client.get(f"{API}/exposures/summary").json()
    assert summary["total"] == 1
    assert summary["unhandled"] == 1
    assert summary["known_exploited_unhandled"] == 1
    # KEV の是正期限を過ぎている
    assert summary["overdue"] == 1


def test_exploited_and_overdue_come_first(client, device_payload):
    import_advisory(
        client,
        ADVISORY
        + [
            {
                "cve_id": "CVE-2026-0002",
                "cvss_score": 5.0,
                "known_exploited": False,
                "affects": [{"vendor": "Cisco", "model": "C9500*"}],
            }
        ],
    )
    add_device(client, device_payload, hostname="sw-1")
    sync(client)

    rows = only(client)
    assert rows[0]["cve_id"] == "CVE-2026-0001"
    assert rows[0]["known_exploited"] is True


# ---------------------------------------------------------------------------
# 漏れ経路 2: 「書いた」と「入れた」が区別できない
# ---------------------------------------------------------------------------


def add_remediation_with_template(client):
    template = client.post(
        f"{API}/config-templates",
        json={
            "name": "Web UI 無効化",
            "vendor": "Cisco",
            "category": "security",
            "body": "no ip http server\nno ip http secure-server\n",
            "variables": [],
        },
    ).json()
    return client.post(
        f"{API}/remediations",
        json={
            "cve_id": "CVE-2026-0001",
            "kind": "workaround",
            "summary": "Web UI を無効化",
            "vendor": "Cisco",
            "model": "C9500*",
            "config_template_id": template["id"],
        },
    ).json()


def upload_config(client, extra=b""):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "core-sw-01.txt"
    return client.post(
        f"{API}/configs/upload",
        files=[("files", ("core-sw-01.txt", fixture.read_bytes() + extra, "text/plain"))],
    )


def test_writing_a_remediation_does_not_mark_devices_done(client, device_payload):
    """対処を書いただけでは、どの機器も対応済みにならない。"""
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)
    add_remediation_with_template(client)

    assert client.get(f"{API}/exposures/summary").json()["unhandled"] == 1


def test_config_verification_backs_up_the_claim(client, device_payload):
    """「入れたつもり」を現行 config で裏取りする。"""
    import_advisory(client)
    add_device(client, device_payload, hostname="core-sw-01")
    # Web UI が有効なままの config
    upload_config(client, b"\nip http server\nip http secure-server\n")
    sync(client)
    remediation = add_remediation_with_template(client)

    row = only(client)[0]
    client.patch(
        f"{API}/exposures/{row['id']}",
        json={"status": "mitigated", "remediation_id": remediation["id"], "decided_by": "運用"},
    )

    # まだ塞がれていないので、裏取りは失敗する
    verified = client.post(f"{API}/exposures/{row['id']}/verify-config").json()
    assert verified["config_verified"] is False

    # 実際に投入された config を取り込み直すと、裏が取れる
    upload_config(client, b"\n")
    verified = client.post(f"{API}/exposures/{row['id']}/verify-config").json()
    assert verified["config_verified"] is True


def test_summary_counts_unverified_claims(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)
    remediation = add_remediation_with_template(client)

    row = only(client)[0]
    client.patch(
        f"{API}/exposures/{row['id']}",
        json={"status": "mitigated", "remediation_id": remediation["id"]},
    )

    summary = client.get(f"{API}/exposures/summary").json()
    assert summary["unhandled"] == 0
    # 済みと言っているが config で裏が取れていない
    assert summary["unverified"] == 1


# ---------------------------------------------------------------------------
# 漏れ経路 3: 暫定対処を恒久対処と混同する
# ---------------------------------------------------------------------------


def test_mitigated_devices_are_still_counted_as_affected(client, device_payload):
    """回避策を当てても版は変わらないので、該当は残っている。"""
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)

    row = only(client)[0]
    client.patch(f"{API}/exposures/{row['id']}", json={"status": "mitigated"})

    summary = client.get(f"{API}/exposures/summary").json()
    assert summary["mitigated_but_still_affected"] == 1


def test_upgrading_resolves_it_automatically(client, device_payload):
    """修正版へ上げれば該当しなくなる。ここは自動で閉じてよい。"""
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="sw-1")
    sync(client)

    client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.9.6"})
    result = sync(client)

    assert result["resolved"] == 1
    assert only(client)[0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# 漏れ経路 4: 状況が変わっても気付かない
# ---------------------------------------------------------------------------


def test_new_cve_creates_new_open_items(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)

    import_advisory(
        client,
        [
            {
                "cve_id": "CVE-2026-9999",
                "cvss_score": 7.5,
                "affects": [{"vendor": "Cisco", "model": "C9500*"}],
            }
        ],
    )
    result = sync(client)
    assert result["created"] == 1


def test_new_device_creates_new_open_items(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)

    add_device(client, device_payload, hostname="sw-2", serial_number="X2")
    assert sync(client)["created"] == 1


def test_version_change_reopens_a_closed_decision(client, device_payload):
    """判断した時点と機器の状態が変わったら、判断をやり直させる。"""
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="sw-1")
    sync(client)

    row = only(client)[0]
    client.patch(f"{API}/exposures/{row['id']}", json={"status": "mitigated"})
    assert sync(client)["reopened"] == 0

    # 版を落として（切り戻し等）状況が変わった
    client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.9.1"})
    result = sync(client)

    assert result["reopened"] == 1
    after = only(client)[0]
    assert after["status"] == "open"
    assert "17.09.04a" in after["reopen_reason"]


def test_resolved_item_reopens_if_it_matches_again(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="sw-1")
    sync(client)

    client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.9.6"})
    sync(client)
    assert only(client)[0]["status"] == "resolved"

    # 切り戻して再び影響範囲に入った
    client.patch(f"{API}/devices/{device_id}", json={"os_version": "17.9.2"})
    assert sync(client)["reopened"] == 1
    assert only(client)[0]["status"] == "open"


def test_retired_devices_drop_out(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload, hostname="sw-1")
    sync(client)

    client.patch(f"{API}/devices/{device_id}", json={"status": "retired"})
    assert sync(client)["resolved"] == 1


# ---------------------------------------------------------------------------
# 判断の記録
# ---------------------------------------------------------------------------


def test_accepting_risk_requires_a_reason(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    sync(client)
    row = only(client)[0]

    rejected = client.patch(f"{API}/exposures/{row['id']}", json={"status": "accepted"})
    assert rejected.status_code == 422

    ok = client.patch(
        f"{API}/exposures/{row['id']}",
        json={"status": "accepted", "note": "対象ポートが閉域のため", "decided_by": "課長"},
    )
    assert ok.status_code == 200
    assert ok.json()["note"] == "対象ポートが閉域のため"


def test_unhandled_only_filter(client, device_payload):
    import_advisory(client)
    add_device(client, device_payload, hostname="sw-1")
    add_device(client, device_payload, hostname="sw-2", serial_number="X2")
    sync(client)

    rows = only(client)
    client.patch(f"{API}/exposures/{rows[0]['id']}", json={"status": "fixed"})

    assert len(only(client, unhandled_only=True)) == 1
