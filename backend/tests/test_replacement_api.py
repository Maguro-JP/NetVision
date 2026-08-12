"""更改支援：年度別の集計と、更改時の影響範囲。"""

from datetime import date, timedelta

from tests.conftest import API


def add_device(client, device_payload, **overrides):
    return client.post(f"{API}/devices", json=device_payload(**overrides)).json()["id"]


def add_contract(client, device_id, end_date, annual_cost=480000):
    return client.post(
        f"{API}/devices/{device_id}/contracts",
        json={
            "provider": "販売代理店",
            "start_date": "2020-04-01",
            "end_date": end_date,
            "annual_cost": annual_cost,
        },
    )


# ---------------------------------------------------------------------------
# 年度別の集計
# ---------------------------------------------------------------------------


def test_devices_are_grouped_by_fiscal_year(client, device_payload):
    """年度は4月始まり。3月末は前年度に入る。"""
    a = add_device(client, device_payload, hostname="sw-a", eol_date="2027-03-31")
    b = add_device(
        client, device_payload, hostname="sw-b", serial_number="B", eol_date="2027-04-01"
    )

    plan = client.get(f"{API}/replacement/plan", params={"years": 10}).json()
    by_year = {y["fiscal_year"]: [t["hostname"] for t in y["targets"]] for y in plan["years"]}

    assert "sw-a" in by_year[2026]
    assert "sw-b" in by_year[2027]
    assert a and b


def test_earlier_of_maintenance_and_eol_decides_the_year(client, device_payload):
    """保守が切れれば直せず、EoL を過ぎれば部品が無い。早い方が実質の期限。"""
    device_id = add_device(client, device_payload, hostname="sw-1", eol_date="2030-12-31")
    add_contract(client, device_id, "2027-06-30")

    plan = client.get(f"{API}/replacement/plan", params={"years": 10}).json()
    target = next(t for y in plan["years"] for t in y["targets"] if t["hostname"] == "sw-1")
    assert target["reason"] == "保守期限"
    assert target["due_on"] == "2027-06-30"


def test_overdue_devices_land_in_the_current_year(client, device_payload):
    """既に期限切れのものは「今年度に対応すべきもの」として扱う。"""
    past = (date.today() - timedelta(days=400)).isoformat()
    add_device(client, device_payload, hostname="old-sw", eol_date=past)

    plan = client.get(f"{API}/replacement/plan").json()
    first = plan["years"][0]
    assert "old-sw" in [t["hostname"] for t in first["targets"]]


def test_devices_without_a_due_date_are_listed_separately(client, device_payload):
    """期限が分からない機器を黙って落とすと、計画から漏れる。"""
    add_device(client, device_payload, hostname="unknown-sw", eol_date=None, eos_date=None)

    plan = client.get(f"{API}/replacement/plan").json()
    assert "unknown-sw" in [t["hostname"] for t in plan["unscheduled"]]


def test_retired_devices_are_excluded(client, device_payload):
    add_device(client, device_payload, hostname="gone-sw", status="retired", eol_date="2027-01-01")
    plan = client.get(f"{API}/replacement/plan", params={"years": 10}).json()
    names = [t["hostname"] for y in plan["years"] for t in y["targets"]]
    assert "gone-sw" not in names


def test_cost_is_summed_per_year(client, device_payload):
    device_id = add_device(client, device_payload, hostname="sw-1", eol_date="2027-06-30")
    add_contract(client, device_id, "2027-06-30", annual_cost=100000)

    plan = client.get(f"{API}/replacement/plan", params={"years": 10}).json()
    year = next(y for y in plan["years"] if y["fiscal_year"] == 2027)
    assert year["estimated_cost"] > 0
    assert plan["total_cost"] >= year["estimated_cost"]


def test_years_parameter_limits_the_range(client, device_payload):
    add_device(client, device_payload, hostname="sw-1", eol_date="2027-01-01")
    plan = client.get(f"{API}/replacement/plan", params={"years": 3}).json()
    assert len(plan["years"]) == 3


# ---------------------------------------------------------------------------
# 影響範囲
# ---------------------------------------------------------------------------


def build_topology(client, device_payload):
    """core - edge - leaf の直列。edge を止めると leaf が孤立する。"""
    core = add_device(client, device_payload, hostname="core-sw", serial_number="C")
    edge = add_device(client, device_payload, hostname="edge-sw", serial_number="E")
    leaf = add_device(client, device_payload, hostname="leaf-sw", serial_number="L")
    other = add_device(client, device_payload, hostname="other-sw", serial_number="O")

    for a, b in ((core, edge), (edge, leaf), (core, other)):
        client.post(
            f"{API}/topology/links",
            json={
                "a_device_id": a,
                "a_interface": "Gi0/1",
                "b_device_id": b,
                "b_interface": "Gi0/1",
            },
        )
    return core, edge, leaf, other


def test_impact_lists_devices_that_lose_their_path(client, device_payload):
    _core, edge, _leaf, _other = build_topology(client, device_payload)

    report = client.get(f"{API}/replacement/impact/{edge}").json()
    assert report["isolated"] == ["leaf-sw"]
    assert "core-sw" in report["directly_connected"]
    assert "1台" in report["note"]


def test_leaf_device_isolates_nobody(client, device_payload):
    _core, _edge, leaf, _other = build_topology(client, device_payload)

    report = client.get(f"{API}/replacement/impact/{leaf}").json()
    assert report["isolated"] == []
    assert "迂回" in report["note"]


def test_redundant_path_means_no_isolation(client, device_payload):
    """冗長経路があれば、1台落としても孤立しない。"""
    core, edge, leaf, _other = build_topology(client, device_payload)
    # core と leaf を直結して迂回路を作る
    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": core,
            "a_interface": "Gi0/9",
            "b_device_id": leaf,
            "b_interface": "Gi0/9",
        },
    )

    report = client.get(f"{API}/replacement/impact/{edge}").json()
    assert report["isolated"] == []


def test_unconnected_device_is_explained(client, device_payload):
    device_id = add_device(client, device_payload, hostname="lonely-sw")
    report = client.get(f"{API}/replacement/impact/{device_id}").json()
    assert report["isolated"] == []
    assert "繋がっていません" in report["note"]


def test_impact_count_appears_in_the_plan(client, device_payload):
    _core, edge, _leaf, _other = build_topology(client, device_payload)
    client.patch(f"{API}/devices/{edge}", json={"eol_date": "2027-06-30"})

    plan = client.get(f"{API}/replacement/plan", params={"years": 10}).json()
    target = next(t for y in plan["years"] for t in y["targets"] if t["hostname"] == "edge-sw")
    assert target["impact_count"] == 1


def test_unknown_device_impact_is_404(client):
    assert client.get(f"{API}/replacement/impact/999").status_code == 404


def test_the_plan_reports_totals_even_when_the_list_is_trimmed(client, device_payload):
    """返す機器を絞っても、台数と金額は全件から出すこと。

    16万台の環境では全部返すと応答が数十MBになり、受け取る側が固まる。
    ただし計画の数字が変わってしまっては意味がない。
    """
    from datetime import date, timedelta

    from app.services import replacement

    soon = (date.today() + timedelta(days=30)).isoformat()
    total = replacement._TARGET_SAMPLE + 10
    for index in range(total):
        client.post(
            f"{API}/devices",
            json=device_payload(
                hostname=f"plan-{index:04d}", serial_number=f"S{index}", eol_date=soon
            ),
        )

    plan = client.get(f"{API}/replacement/plan").json()

    assert plan["total_devices"] == total
    year = next(y for y in plan["years"] if y["device_count"] > 0)
    assert year["device_count"] == total  # 数は全件
    assert year["target_total"] == total
    assert len(year["targets"]) == replacement._TARGET_SAMPLE  # 中身は絞る
