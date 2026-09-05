from datetime import date

from tests.conftest import API
from tests.test_reference_api import add_device, upload_vulnerabilities

# NIST NVD 2.0 の実際の形に合わせたペイロード
NVD_PAYLOAD = {
    "vulnerabilities": [
        {
            "cve": {
                "id": "CVE-2024-20353",
                "published": "2024-04-24T19:15:08.000",
                "lastModified": "2024-05-01T10:00:00.000",
                "descriptions": [
                    {"lang": "en", "value": "A vulnerability in the management web server."},
                    {"lang": "ja", "value": "無視される言語"},
                ],
                "metrics": {
                    "cvssMetricV31": [{"cvssData": {"baseScore": 8.6, "baseSeverity": "HIGH"}}]
                },
                "configurations": [
                    {
                        "nodes": [
                            {
                                "cpeMatch": [
                                    {
                                        "vulnerable": True,
                                        "criteria": "cpe:2.3:o:cisco:ios_xe:*:*:*:*:*:*:*:*",
                                        "versionStartIncluding": "17.9.0",
                                        "versionEndExcluding": "17.9.5",
                                    },
                                    {
                                        "vulnerable": False,
                                        "criteria": "cpe:2.3:h:cisco:catalyst:-:*:*:*:*:*:*:*",
                                    },
                                ]
                            }
                        ]
                    }
                ],
            }
        }
    ]
}

# CISA KEV カタログの形
KEV_PAYLOAD = {
    "title": "CISA Catalog of Known Exploited Vulnerabilities",
    "vulnerabilities": [
        {
            "cveID": "CVE-2024-20353",
            "vendorProject": "Cisco",
            "product": "Adaptive Security Appliance",
            "vulnerabilityName": "Cisco ASA and FTD DoS Vulnerability",
            "dateAdded": "2024-04-24",
            "shortDescription": "Cisco ASA contains a DoS vulnerability.",
            "dueDate": "2024-05-01",
        }
    ],
}

# ベンダー PSIRT を手で整理した簡易形式
SIMPLE_PAYLOAD = [
    {
        "cve_id": "CVE-2025-0001",
        "title": "IOS-XE の権限昇格",
        "cvss_score": 9.8,
        "cvss_severity": "CRITICAL",
        "published": "2025-01-15",
        "source": "vendor",
        "affects": [
            {
                "vendor": "Cisco",
                "model": "C9500*",
                "os_name": "IOS-XE",
                "version_end_excluding": "17.12.01",
                "fixed_version": "17.12.01",
            }
        ],
    }
]


def matches(client, **params):
    return client.get(f"{API}/reference/vulnerabilities/matches/summary", params=params).json()


# ---------------------------------------------------------------------------
# 取り込み
# ---------------------------------------------------------------------------


def test_nvd_json_is_imported(client):
    body = upload_vulnerabilities(client, NVD_PAYLOAD).json()

    assert body["format"] == "nvd"
    assert body["created"] == 1
    assert body["affects_created"] == 1  # vulnerable=false のエントリは無視する

    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]
    assert cve["cve_id"] == "CVE-2024-20353"
    assert cve["cvss_score"] == 8.6
    assert cve["cvss_severity"] == "HIGH"
    assert cve["published"] == "2024-04-24"
    assert cve["source"] == "nvd"
    assert "nvd.nist.gov" in cve["source_url"]


def test_kev_marks_known_exploited(client):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    body = upload_vulnerabilities(client, KEV_PAYLOAD).json()

    assert body["format"] == "kev"
    assert body["updated"] == 1  # 既存の CVE に印を付ける

    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]
    assert cve["known_exploited"] is True
    assert cve["remediation_due"] == "2024-05-01"
    assert cve["cvss_score"] == 8.6  # NVD 由来の情報は保持される


def test_simple_vendor_format_is_imported(client):
    body = upload_vulnerabilities(client, SIMPLE_PAYLOAD).json()

    assert body["format"] == "simple"
    assert body["created"] == 1

    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]
    assert cve["cve_id"] == "CVE-2025-0001"
    assert cve["source"] == "vendor"


def test_broken_json_is_rejected(client):
    res = client.post(
        f"{API}/reference/vulnerabilities/import",
        files={"file": ("cve.json", b"{not json", "application/json")},
    )
    assert res.status_code == 422
    assert "JSON として読めませんでした" in res.json()["detail"]


def test_unknown_shape_is_rejected(client):
    res = upload_vulnerabilities(client, {"something": "else"})
    assert res.status_code == 422


def test_fetched_at_is_recorded(client):
    upload_vulnerabilities(client, SIMPLE_PAYLOAD, fetched_at="2026-08-01")
    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]
    assert cve["fetched_at"] == "2026-08-01"


def test_reimport_updates_existing_cve(client):
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)
    body = upload_vulnerabilities(client, SIMPLE_PAYLOAD).json()

    assert body["created"] == 0
    assert body["updated"] == 1
    assert len(client.get(f"{API}/reference/vulnerabilities").json()) == 1


# ---------------------------------------------------------------------------
# 突合
# ---------------------------------------------------------------------------


def test_version_in_range_is_matched(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, hostname="sw-vuln", os_name="IOS-XE", os_version="17.9.4")

    body = matches(client)
    assert body["devices_affected"] == 1

    finding = body["findings"][0]
    assert finding["cve_id"] == "CVE-2024-20353"
    assert finding["hostname"] == "sw-vuln"
    assert "17.9.4" in finding["matched_on"]


def test_patched_version_is_not_matched(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, hostname="sw-fixed", os_name="IOS-XE", os_version="17.9.5")

    assert matches(client)["devices_affected"] == 0


def test_zero_padded_version_is_matched(client, device_payload):
    """`17.09.04` と `17.9.4` を同じものとして扱えないと取りこぼす。"""
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, os_name="IOS-XE", os_version="17.09.04")

    assert matches(client)["devices_affected"] == 1


def test_other_os_is_not_matched(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, vendor="Juniper", os_name="Junos", os_version="17.9.4")

    assert matches(client)["devices_affected"] == 0


def test_model_wildcard_matching(client, device_payload):
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)
    add_device(
        client,
        device_payload,
        hostname="sw-95",
        model="C9500-40X",
        os_name="IOS-XE",
        os_version="17.9.4",
    )
    add_device(
        client,
        device_payload,
        hostname="sw-93",
        model="C9300-48P",
        os_name="IOS-XE",
        os_version="17.9.4",
        serial_number="X2",
    )

    body = matches(client)
    assert [f["hostname"] for f in body["findings"]] == ["sw-95"]
    assert body["findings"][0]["fixed_version"] == "17.12.01"


def test_devices_without_version_are_counted(client, device_payload):
    """バージョン未登録は判定できない。件数を出さないと見落とす。"""
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, os_name="IOS-XE", os_version=None)

    body = matches(client)
    assert body["devices_affected"] == 0
    assert body["devices_without_version"] == 1


def test_retired_devices_are_excluded(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, status="retired", os_name="IOS-XE", os_version="17.9.4")

    assert matches(client)["devices_affected"] == 0


def test_known_exploited_comes_first(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)  # CVSS 8.6
    upload_vulnerabilities(client, KEV_PAYLOAD)  # 同じ CVE を悪用確認済みにする
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)  # CVSS 9.8 だが悪用確認なし

    add_device(client, device_payload, model="C9500-40X", os_name="IOS-XE", os_version="17.9.4")

    body = matches(client)
    assert [f["cve_id"] for f in body["findings"]] == ["CVE-2024-20353", "CVE-2025-0001"]
    assert body["known_exploited_findings"] == 1
    assert body["critical_findings"] == 1  # CVSS 9.0 以上


def test_summary_can_be_scoped_to_one_device(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    first = add_device(
        client, device_payload, hostname="sw-a", os_name="IOS-XE", os_version="17.9.4"
    )
    add_device(
        client,
        device_payload,
        hostname="sw-b",
        os_name="IOS-XE",
        os_version="17.9.4",
        serial_number="X2",
    )

    body = matches(client, device_id=first)
    assert [f["hostname"] for f in body["findings"]] == ["sw-a"]


def test_affected_device_count_on_the_list(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, os_name="IOS-XE", os_version="17.9.4")

    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]
    assert cve["affected_device_count"] == 1

    assert (
        client.get(f"{API}/reference/vulnerabilities", params={"affecting_only": "true"}).json()
        != []
    )


def test_non_affecting_cves_can_be_hidden(client, device_payload):
    upload_vulnerabilities(client, NVD_PAYLOAD)
    add_device(client, device_payload, os_name="IOS-XE", os_version="17.9.9")  # 対象外

    assert (
        client.get(f"{API}/reference/vulnerabilities", params={"affecting_only": "true"}).json()
        == []
    )


def test_freshness_counts_vulnerabilities(client):
    upload_vulnerabilities(client, SIMPLE_PAYLOAD, fetched_at=date.today().isoformat())
    body = client.get(f"{API}/reference/freshness").json()

    assert body["vulnerability_count"] == 1
    assert body["vulnerabilities_stale"] == 0
    assert body["stale_after_days"] == 180


def test_vulnerability_can_be_deleted(client):
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)
    cve = client.get(f"{API}/reference/vulnerabilities").json()[0]

    assert client.delete(f"{API}/reference/vulnerabilities/{cve['id']}").status_code == 204
    assert client.get(f"{API}/reference/vulnerabilities").json() == []


def test_a_device_without_a_version_is_not_called_safe(client, device_payload):
    """版数が読めていないだけの機器を、当たらない側に混ぜない。

    型番まで一致しているのに版数が無いと、範囲に入るかを判定できない。
    そこを「該当なし」と扱うと、読めていないだけの機器が安全な機器の中に
    紛れる。危ないほうの取り違えなので、分けて出す。
    """
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)
    add_device(
        client, device_payload, hostname="noversion-sw-01", model="C9500-40X", os_version=None
    )

    summary = client.get(f"{API}/reference/vulnerabilities/matches/summary").json()
    ours = [f for f in summary["findings"] if f["hostname"] == "noversion-sw-01"]
    assert ours, "型番が当たるのに、1件も出ていない"
    assert all(f["undetermined"] for f in ours)
    assert "バージョンが未登録" in ours[0]["matched_on"]
    assert summary["undetermined_findings"] >= 1


def test_an_undetermined_finding_is_not_counted_as_affected(client, device_payload):
    """判定できていないものを「影響を受ける機器」に数えない。

    当たると分かっている台数と混ざると、どちらの数字も意味が薄くなる。
    """
    upload_vulnerabilities(client, SIMPLE_PAYLOAD)
    add_device(
        client, device_payload, hostname="noversion-sw-02", model="C9500-40X", os_version=None
    )

    summary = client.get(f"{API}/reference/vulnerabilities/matches/summary").json()
    assert summary["devices_affected"] == 0
    assert summary["known_exploited_findings"] == 0
    assert summary["critical_findings"] == 0
    assert summary["undetermined_findings"] >= 1
