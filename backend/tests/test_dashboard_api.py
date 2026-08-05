from datetime import date, timedelta

from tests.conftest import API


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_summary_counts_and_ordering(client, device_payload):
    client.post(f"{API}/devices", json=device_payload(hostname="sw-expired", eol_date=_in(-10)))
    client.post(
        f"{API}/devices",
        json=device_payload(hostname="sw-critical", vendor="Juniper", eol_date=_in(45)),
    )
    client.post(f"{API}/devices", json=device_payload(hostname="sw-ok", eol_date=_in(2000)))
    client.post(f"{API}/devices", json=device_payload(hostname="sw-unknown"))

    summary = client.get(f"{API}/summary").json()
    assert summary["total_devices"] == 4
    assert summary["active_devices"] == 4
    assert summary["by_overall_severity"]["expired"] == 1
    assert summary["by_overall_severity"]["critical"] == 1
    # 保守契約が未登録なので、EoL が十分先の機器も総合判定は unknown（データ不足）になる
    assert summary["by_overall_severity"]["unknown"] == 2
    assert summary["by_eol_severity"]["ok"] == 1
    assert summary["devices_without_lifecycle_data"] == 1
    assert summary["by_vendor"][0] == {"vendor": "Cisco", "count": 3}

    # 期限が近い順に並ぶ
    assert [d["hostname"] for d in summary["upcoming"]] == ["sw-expired", "sw-critical"]


def test_alerts_window(client, device_payload):
    client.post(f"{API}/devices", json=device_payload(hostname="sw-soon", eol_date=_in(20)))
    client.post(f"{API}/devices", json=device_payload(hostname="sw-later", eol_date=_in(400)))

    assert [
        d["hostname"] for d in client.get(f"{API}/alerts", params={"within_days": 30}).json()
    ] == ["sw-soon"]
    assert len(client.get(f"{API}/alerts", params={"within_days": 500}).json()) == 2


def test_retired_devices_excluded_from_alerts(client, device_payload):
    client.post(
        f"{API}/devices",
        json=device_payload(hostname="sw-old", status="retired", eol_date=_in(-100)),
    )
    assert client.get(f"{API}/alerts").json() == []


def test_severity_filter_on_device_list(client, device_payload):
    client.post(f"{API}/devices", json=device_payload(hostname="sw-expired", eol_date=_in(-1)))
    client.post(f"{API}/devices", json=device_payload(hostname="sw-ok", eol_date=_in(2000)))

    res = client.get(f"{API}/devices", params={"severity": "expired"}).json()
    assert [d["hostname"] for d in res] == ["sw-expired"]


def test_site_assignment_and_device_count(client, device_payload):
    site_id = client.post(f"{API}/sites", json={"name": "本社DC"}).json()["id"]
    client.post(f"{API}/devices", json=device_payload(site_id=site_id))

    sites = client.get(f"{API}/sites").json()
    assert sites[0]["device_count"] == 1
    assert client.get(f"{API}/devices").json()[0]["site_name"] == "本社DC"

    # 拠点を消しても機器は残る
    assert client.delete(f"{API}/sites/{site_id}").status_code == 204
    assert client.get(f"{API}/devices").json()[0]["site_name"] is None
