"""VLAN がトポロジに反映されること。

「VLAN 20 はどこまで伸びているのか」に答えられるかを確かめる。
"""

from pathlib import Path

from tests.conftest import API

FIXTURES = Path(__file__).parent / "fixtures"


def ingest_all(client):
    files = [
        ("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted(FIXTURES.glob("*.txt"))
    ]
    return client.post(f"{API}/configs/upload", files=files, data={"create_missing": "true"})


def test_vlans_are_read_from_config(client):
    ingest_all(client)
    vlans = client.get(f"{API}/topology/vlans").json()

    ids = {v["vlan_id"] for v in vlans}
    assert 10 in ids

    users = next(v for v in vlans if v["vlan_id"] == 10)
    assert users["name"]  # 番号だけでは何のセグメントか分からないので名前も持つ
    assert users["device_names"]


def test_vlan_shows_which_devices_carry_it(client):
    """どこまで伸びているかが分かること。"""
    ingest_all(client)
    vlans = client.get(f"{API}/topology/vlans").json()

    for vlan in vlans:
        assert len(vlan["device_ids"]) == len(vlan["device_names"])
        assert vlan["device_ids"]


def test_topology_nodes_carry_their_vlans(client):
    ingest_all(client)
    nodes = client.get(f"{API}/topology").json()["nodes"]

    with_vlans = [n for n in nodes if n["vlans"]]
    assert with_vlans, "VLAN を持つ機器が1台も無い"
    assert all(isinstance(v, int) for n in with_vlans for v in n["vlans"])


def test_vlans_follow_the_latest_config(client):
    """VLAN の定義は最新の Config に揃えること。

    ただし**トランクの許可 VLAN からは消えていない**ので、一覧からは落とさない。
    定義が無いのに通しているポートがあるのは、それ自体が直すべき状態なので、
    見えなくしてはいけない。定義の有無は defined_device_ids で区別する。
    """
    client.post(
        f"{API}/configs/upload",
        files=[
            (
                "files",
                (
                    "qx-sw-01.txt",
                    (FIXTURES / "qx-sw-01.txt").read_bytes(),
                    "text/plain",
                ),
            )
        ],
        data={"create_missing": "true"},
    )
    before = {v["vlan_id"] for v in client.get(f"{API}/topology/vlans").json()}
    assert {10, 20} <= before

    trimmed = (FIXTURES / "qx-sw-01.txt").read_text().replace("vlan 20\n name SERVERS\n#\n", "")
    client.post(
        f"{API}/configs/upload",
        files=[("files", ("qx-sw-01.txt", trimmed.encode(), "text/plain"))],
    )

    after = client.get(f"{API}/topology/vlans").json()
    servers = next(v for v in after if v["vlan_id"] == 20)
    assert servers["defined_device_ids"] == []  # 定義は消えた
    assert servers["device_ids"]  # トランクは通したままなので残る

    users = next(v for v in after if v["vlan_id"] == 10)
    assert users["defined_device_ids"]


def test_export_can_be_filtered_by_vlan(client):
    """セグメント単位で資料を作れること。"""
    ingest_all(client)
    vlans = client.get(f"{API}/topology/vlans").json()
    target = vlans[0]["vlan_id"]

    res = client.get(f"{API}/topology/export.svg", params={"vlan": target})
    assert res.status_code == 200
    assert f"VLAN {target}" in res.text
    assert f"vlan{target}" in res.headers["content-disposition"]

    # 絞り込んだ分だけ台数が減る
    full = client.get(f"{API}/topology/export.svg").text
    assert res.text != full


def test_devices_without_vlans_are_still_listed(client, device_payload):
    """VLAN を持たない機器があっても一覧は壊れない。"""
    client.post(f"{API}/devices", json=device_payload(hostname="no-vlan-sw"))
    assert client.get(f"{API}/topology/vlans").status_code == 200
    nodes = client.get(f"{API}/topology").json()["nodes"]
    assert any(n["vlans"] == [] for n in nodes)
