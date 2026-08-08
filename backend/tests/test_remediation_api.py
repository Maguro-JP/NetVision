"""CVE から対処までの導線。

「脆弱性を見つける」だけなら他にもツールはあるので、そこから先
（この機器で何をすればいいか）に価値がある。
"""

import json

from tests.conftest import API

ADVISORY = [
    {
        "cve_id": "CVE-2026-0001",
        "title": "IOS-XE の Web UI に認証回避",
        "cvss_score": 9.8,
        "cvss_severity": "CRITICAL",
        "known_exploited": True,
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


def add_remediation(client, **fields):
    payload = {
        "cve_id": "CVE-2026-0001",
        "kind": "workaround",
        "summary": "Web UI を無効化する",
        "vendor": "Cisco",
        "model": "C9500*",
        "applies_from": "17.9.0",
        "applies_to": "17.9.5",
        "procedure": "no ip http server\nno ip http secure-server\n",
    }
    payload.update(fields)
    return client.post(f"{API}/remediations", json=payload)


# ---------------------------------------------------------------------------
# 見つけるだけで終わらせない
# ---------------------------------------------------------------------------


def test_finding_carries_its_remediation(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload)
    add_remediation(client)

    report = client.get(f"{API}/devices/{device_id}/remediation").json()
    finding = report["findings"][0]

    assert finding["cve_id"] == "CVE-2026-0001"
    assert finding["known_exploited"] is True
    assert finding["has_remediation"] is True
    assert finding["remediations"][0]["summary"] == "Web UI を無効化する"
    assert "no ip http server" in finding["remediations"][0]["procedure"]
    assert report["unresolved_count"] == 0


def test_fixed_version_and_upgrade_availability(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload)

    finding = client.get(f"{API}/devices/{device_id}/remediation").json()["findings"][0]
    assert finding["fixed_version"] == "17.9.6"
    # 17.09.04a < 17.9.6 なので上げれば直る
    assert finding["upgrade_available"] is True


def test_device_already_on_the_fixed_version_is_not_flagged(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload, os_version="17.9.6")

    report = client.get(f"{API}/devices/{device_id}/remediation").json()
    assert report["findings"] == []
    assert "該当する脆弱性はありません" in report["note"]


# ---------------------------------------------------------------------------
# 一度書いた対処が再利用される（ここが狙い）
# ---------------------------------------------------------------------------


def test_one_remediation_covers_every_matching_device(client, device_payload):
    import_advisory(client)
    first = add_device(client, device_payload, hostname="sw-1")
    second = add_device(client, device_payload, hostname="sw-2", serial_number="X2")
    third = add_device(
        client, device_payload, hostname="sw-3", serial_number="X3", os_version="17.9.2"
    )

    add_remediation(client)  # 1回書くだけ

    for device_id in (first, second, third):
        report = client.get(f"{API}/devices/{device_id}/remediation").json()
        assert report["findings"][0]["has_remediation"] is True, device_id


def test_remediation_outside_the_version_range_is_not_offered(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload)
    add_remediation(client, applies_from="17.12.0", applies_to="17.12.99")

    report = client.get(f"{API}/devices/{device_id}/remediation").json()
    assert report["findings"][0]["has_remediation"] is False


def test_remediation_for_another_model_is_not_offered(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload)
    add_remediation(client, model="C9300-48P")

    assert client.get(f"{API}/devices/{device_id}/remediation").json()["findings"][0][
        "has_remediation"
    ] is False


# ---------------------------------------------------------------------------
# 対処が無いときに手ぶらで返さない
# ---------------------------------------------------------------------------


def test_missing_remediation_suggests_where_to_look(client, device_payload):
    """対処が未登録でも、調べる手掛かりは出す。"""
    import_advisory(client)
    device_id = add_device(client, device_payload)

    client.post(
        f"{API}/documents",
        data={
            "vendor": "Cisco",
            "title": "Catalyst 9500 コンフィグガイド 17.9",
            "model": "C9500*",
            "applies_from": "17.9.0",
            "applies_to": "17.9.99",
        },
    )

    report = client.get(f"{API}/devices/{device_id}/remediation").json()
    finding = report["findings"][0]

    assert finding["has_remediation"] is False
    assert report["unresolved_count"] == 1
    assert "再利用" in report["note"]
    assert any("17.9" in d["title"] for d in finding["suggested_documents"])


def test_remediation_can_cite_a_manual_and_a_template(client, device_payload):
    import_advisory(client)
    device_id = add_device(client, device_payload)

    document = client.post(
        f"{API}/documents", data={"vendor": "Cisco", "title": "セキュリティアドバイザリ"}
    ).json()
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

    add_remediation(client, source_document_id=document["id"], config_template_id=template["id"])

    option = client.get(f"{API}/devices/{device_id}/remediation").json()["findings"][0][
        "remediations"
    ][0]
    assert option["source_document_title"] == "セキュリティアドバイザリ"
    assert option["config_template_name"] == "Web UI 無効化"
    assert option["config_template_id"] == template["id"]


# ---------------------------------------------------------------------------
# 並び順と管理
# ---------------------------------------------------------------------------


def test_verified_workaround_comes_first(client, device_payload):
    """今すぐ効く確認済みの回避策を先に出す。"""
    import_advisory(client)
    device_id = add_device(client, device_payload)

    add_remediation(client, kind="upgrade", summary="17.9.6 へ上げる")
    add_remediation(client, summary="Web UI を無効化する", verified_by="運用チーム",
                    verified_on="2026-08-01")

    options = client.get(f"{API}/devices/{device_id}/remediation").json()["findings"][0][
        "remediations"
    ]
    assert options[0]["summary"] == "Web UI を無効化する"
    assert options[0]["verified_by"] == "運用チーム"


def test_remediation_crud(client):
    import_advisory(client)
    created = add_remediation(client).json()

    assert created["cve_id"] == "CVE-2026-0001"
    updated = client.patch(
        f"{API}/remediations/{created['id']}", json={"summary": "改訂"}
    ).json()
    assert updated["summary"] == "改訂"

    assert len(client.get(f"{API}/remediations", params={"cve_id": "CVE-2026-0001"}).json()) == 1
    assert client.delete(f"{API}/remediations/{created['id']}").status_code == 204
    assert client.get(f"{API}/remediations").json() == []


def test_remediation_for_unknown_cve_is_rejected(client):
    res = add_remediation(client, cve_id="CVE-9999-9999")
    assert res.status_code == 422
    assert "取り込んでください" in res.json()["detail"]


def test_unknown_device_is_404(client):
    assert client.get(f"{API}/devices/999/remediation").status_code == 404
