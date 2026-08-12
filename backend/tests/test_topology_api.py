from tests.conftest import API
from tests.test_configs_api import seed_fleet, upload


def graph(client, **params):
    return client.get(f"{API}/topology", params=params).json()


def test_links_are_inferred_from_cdp_and_lldp(client, device_payload):
    """Config を投入するだけで、結線が自動で組み上がる。"""
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01", "edge-rt-01", "acc-sw-osaka-01")

    body = graph(client)
    assert {n["hostname"] for n in body["nodes"]} == {
        "core-sw-01",
        "edge-rt-01",
        "acc-sw-osaka-01",
    }

    pairs = {(e["source"], e["target"]) for e in body["edges"]}
    assert (ids["core"], ids["edge"]) in pairs
    assert (ids["core"], ids["acc"]) in pairs
    assert len(body["edges"]) == 2  # 逆向きの重複を作らない


def test_both_way_links_are_marked_as_confirmed(client, device_payload):
    """両側の機器から同じ結線が見えたものは、信頼度が高いものとして印を付ける。"""
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01", "edge-rt-01")

    edges = {(e["source"], e["target"]): e for e in graph(client)["edges"]}

    # 両側の Config を入れた core ↔ edge は、双方向で確認できている
    confirmed = edges[(ids["core"], ids["edge"])]
    assert confirmed["confirmed_both_ways"] is True
    assert confirmed["source_interface"] == "GigabitEthernet1/0/1"
    assert confirmed["target_interface"] == "ge-0/0/0"

    # core からしか見えていない acc への線は、片側のみ
    assert edges[(ids["core"], ids["acc"])]["confirmed_both_ways"] is False


def test_interface_abbreviations_are_matched(client, device_payload):
    """`Gig 1/0/2` と `GigabitEthernet1/0/2` を同じポートとして扱う。"""
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01", "acc-sw-osaka-01")

    edges = {(e["source"], e["target"]): e for e in graph(client)["edges"]}
    # core は `GigabitEthernet1/0/2`、acc は `Gig 1/0/2` と書くが同じ線として繋がる
    assert edges[(ids["core"], ids["acc"])]["confirmed_both_ways"] is True


def test_unknown_neighbors_are_reported(client, device_payload):
    """台帳に無い隣接先は結線せず、記載漏れとして報告する。"""
    seed_fleet(client, device_payload)
    body = upload(client, "acc-sw-osaka-01").json()[0]

    unresolved = {n["remote_hostname"] for n in body["unresolved_neighbors"]}
    assert unresolved == {"unmanaged-hub"}


def test_rebuild_picks_up_devices_registered_later(client, device_payload):
    """先に Config を入れ、後から機器を登録しても、作り直せば繋がる。"""
    core = client.post(f"{API}/devices", json=device_payload(hostname="core-sw-01")).json()["id"]
    upload(client, "core-sw-01")
    assert graph(client)["edges"] == []

    client.post(
        f"{API}/devices",
        json=device_payload(hostname="edge-rt-01", serial_number="X2", device_type="router"),
    )
    result = client.post(f"{API}/topology/rebuild").json()

    assert result["links_created"] == 1
    edges = graph(client)["edges"]
    assert len(edges) == 1
    assert edges[0]["source"] == core


def test_rebuild_keeps_manually_drawn_links(client, device_payload):
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01")

    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": ids["edge"],
            "b_device_id": ids["acc"],
            "a_interface": "ge-0/0/5",
            "b_interface": "Gi0/5",
            "label": "予備回線",
        },
    )

    client.post(f"{API}/topology/rebuild")

    edges = graph(client)["edges"]
    manual = [e for e in edges if e["origin"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["label"] == "予備回線"


def test_manual_link_can_be_drawn_and_removed(client, device_payload):
    ids = seed_fleet(client, device_payload)

    res = client.post(
        f"{API}/topology/links",
        json={"a_device_id": ids["core"], "b_device_id": ids["acc"], "a_interface": "Gi1/0/9"},
    )
    assert res.status_code == 201
    link_id = res.json()["id"]
    assert res.json()["origin"] == "manual"

    updated = client.patch(
        f"{API}/topology/links/{link_id}", json={"label": "直結", "b_interface": "Gi0/9"}
    ).json()
    assert updated["label"] == "直結"
    assert updated["b_interface"] == "Gi0/9"

    assert client.delete(f"{API}/topology/links/{link_id}").status_code == 204
    assert graph(client)["edges"] == []


def test_duplicate_manual_link_is_rejected(client, device_payload):
    ids = seed_fleet(client, device_payload)
    payload = {"a_device_id": ids["core"], "b_device_id": ids["acc"]}

    assert client.post(f"{API}/topology/links", json=payload).status_code == 201
    assert client.post(f"{API}/topology/links", json=payload).status_code == 409


def test_self_link_is_rejected(client, device_payload):
    ids = seed_fleet(client, device_payload)
    res = client.post(
        f"{API}/topology/links", json={"a_device_id": ids["core"], "b_device_id": ids["core"]}
    )
    assert res.status_code == 422


def test_positions_are_saved_and_survive_new_devices(client, device_payload):
    ids = seed_fleet(client, device_payload)

    body = graph(client)
    assert body["unplaced_count"] == 3  # 最初は全て自動配置

    client.put(
        f"{API}/topology/positions",
        json={"layer": "physical", "positions": [{"device_id": ids["core"], "x": 100, "y": 200}]},
    )

    body = graph(client)
    core = next(n for n in body["nodes"] if n["device_id"] == ids["core"])
    assert (core["x"], core["y"], core["placed"]) == (100, 200, True)

    # 機器が増えても、動かした位置はそのまま
    client.post(f"{API}/devices", json=device_payload(hostname="sw-new", serial_number="X9"))
    core = next(n for n in graph(client)["nodes"] if n["device_id"] == ids["core"])
    assert (core["x"], core["y"]) == (100, 200)


def test_positions_can_be_reset(client, device_payload):
    ids = seed_fleet(client, device_payload)
    client.put(
        f"{API}/topology/positions",
        json={"positions": [{"device_id": ids["core"], "x": 100, "y": 200}]},
    )

    assert client.delete(f"{API}/topology/positions").json() == {"reset": 1}
    assert graph(client)["unplaced_count"] == 3


def test_layers_have_independent_positions(client, device_payload):
    ids = seed_fleet(client, device_payload)
    client.put(
        f"{API}/topology/positions",
        json={"layer": "l3", "positions": [{"device_id": ids["core"], "x": 50, "y": 60}]},
    )

    l3 = next(n for n in graph(client, layer="l3")["nodes"] if n["device_id"] == ids["core"])
    physical = next(
        n for n in graph(client, layer="physical")["nodes"] if n["device_id"] == ids["core"]
    )
    assert (l3["x"], l3["y"]) == (50, 60)
    assert physical["placed"] is False


def test_auto_layout_puts_routers_above_switches(client, device_payload):
    client.post(f"{API}/devices", json=device_payload(hostname="rt-01", device_type="router"))
    client.post(
        f"{API}/devices",
        json=device_payload(hostname="sw-01", device_type="switch", serial_number="X2"),
    )
    client.post(
        f"{API}/devices",
        json=device_payload(hostname="ap-01", device_type="wireless_ap", serial_number="X3"),
    )

    nodes = {n["hostname"]: n for n in graph(client)["nodes"]}
    assert nodes["rt-01"]["y"] < nodes["sw-01"]["y"] < nodes["ap-01"]["y"]


def test_retired_devices_are_hidden_by_default(client, device_payload):
    client.post(f"{API}/devices", json=device_payload(hostname="sw-old", status="retired"))
    assert graph(client)["nodes"] == []
    assert len(graph(client, include_retired=True)["nodes"]) == 1


def test_nodes_carry_alert_severity_for_colouring(client, device_payload):
    from datetime import date, timedelta

    expired = (date.today() - timedelta(days=10)).isoformat()
    client.post(f"{API}/devices", json=device_payload(hostname="sw-eol", eol_date=expired))

    node = graph(client)["nodes"][0]
    assert node["severity"] == "expired"


def test_stats_report_isolated_devices(client, device_payload):
    seed_fleet(client, device_payload)
    upload(client, "core-sw-01", "edge-rt-01")

    stats = client.get(f"{API}/topology/stats").json()
    assert stats["device_count"] == 3
    assert stats["link_count"] == 2
    assert stats["discovered_links"] == 2
    assert stats["confirmed_links"] == 1  # 両側の Config が揃っているのは core ↔ edge だけ
    assert stats["isolated_devices"] == []


def test_stats_report_devices_without_any_link(client, device_payload):
    seed_fleet(client, device_payload)
    client.post(f"{API}/devices", json=device_payload(hostname="sw-lonely", serial_number="X9"))
    upload(client, "core-sw-01")

    # Config にも隣接情報にも出てこない機器は、孤立として挙がる
    assert client.get(f"{API}/topology/stats").json()["isolated_devices"] == ["sw-lonely"]


def test_deleting_a_device_removes_its_links(client, device_payload):
    ids = seed_fleet(client, device_payload)
    upload(client, "core-sw-01", "edge-rt-01")
    assert len(graph(client)["edges"]) == 2

    client.delete(f"{API}/devices/{ids['edge']}")
    edges = graph(client)["edges"]
    assert [(e["source"], e["target"]) for e in edges] == [(ids["core"], ids["acc"])]


def test_scoping_only_reads_the_devices_it_needs(client, db_session, device_payload):
    """拠点で絞ったら、その拠点と対向だけを読むこと。

    全部読んでから捨てる作りだと、全国規模では図が1つも出せない
    （16万台では返ってこなかった）。
    """
    from app.services import topology

    other = client.post(f"{API}/sites", json={"name": "遠くの拠点"}).json()["id"]
    here = client.post(f"{API}/sites", json={"name": "見たい拠点"}).json()["id"]

    client.post(f"{API}/devices", json=device_payload(hostname="here-sw-01", site_id=here))
    for index in range(5):
        client.post(
            f"{API}/devices",
            json=device_payload(
                hostname=f"far-sw-{index:02d}", serial_number=f"F{index}", site_id=other
            ),
        )

    only = topology.scoped_device_ids(db_session, site_id=here)

    assert only is not None
    assert len(only) == 1, "関係ない拠点まで読み込んでいる"


def test_scoping_keeps_the_devices_on_the_other_end(client, db_session, device_payload):
    """対向は残す。ここを落とすと線が1本も出ない図になる。"""
    from app.services import topology

    center = client.post(f"{API}/sites", json={"name": "センター"}).json()["id"]
    branch = client.post(f"{API}/sites", json={"name": "支社"}).json()["id"]

    a = client.post(
        f"{API}/devices", json=device_payload(hostname="center-rt", site_id=center)
    ).json()["id"]
    b = client.post(
        f"{API}/devices",
        json=device_payload(hostname="branch-rt", serial_number="B1", site_id=branch),
    ).json()["id"]
    client.post(
        f"{API}/topology/links",
        json={
            "a_device_id": a,
            "a_interface": "Gi0/0",
            "b_device_id": b,
            "b_interface": "Gi0/1",
        },
    )

    only = topology.scoped_device_ids(db_session, site_id=branch)

    assert only == {a, b}

    graph = client.get(f"{API}/topology", params={"site_id": branch}).json()
    assert len(graph["edges"]) == 1


def test_stats_are_counted_without_building_the_whole_graph(client, device_payload):
    """要約は数えるだけ。図そのものは組み立てない。

    以前は数えるためにグラフを作っており、全国規模では15秒かかっていた。
    """
    a = client.post(f"{API}/devices", json=device_payload(hostname="sw-a")).json()["id"]
    b = client.post(
        f"{API}/devices", json=device_payload(hostname="sw-b", serial_number="X2")
    ).json()["id"]
    client.post(f"{API}/devices", json=device_payload(hostname="lonely", serial_number="X3"))
    client.post(
        f"{API}/topology/links",
        json={"a_device_id": a, "a_interface": "Gi0/0", "b_device_id": b, "b_interface": "Gi0/1"},
    )

    stats = client.get(f"{API}/topology/stats").json()

    assert stats["device_count"] == 3
    assert stats["link_count"] == 1
    assert stats["manual_links"] == 1
    assert stats["isolated_devices"] == ["lonely"]
    assert stats["isolated_count"] == 1


def test_retired_devices_are_not_counted_as_isolated(client, device_payload):
    """撤去済みは図に出ないので、繋がっていない機器にも数えない。"""
    client.post(f"{API}/devices", json=device_payload(hostname="gone-01", status="retired"))

    stats = client.get(f"{API}/topology/stats").json()

    assert stats["device_count"] == 0
    assert stats["isolated_count"] == 0


def test_isolated_devices_are_capped_but_counted_in_full(client, device_payload):
    """名前は頭から少しだけ。数は全部数える。"""
    from app.services import topology

    for index in range(topology._ISOLATED_SAMPLE + 5):
        client.post(
            f"{API}/devices",
            json=device_payload(hostname=f"lonely-{index:04d}", serial_number=f"S{index}"),
        )

    stats = client.get(f"{API}/topology/stats").json()

    assert stats["isolated_count"] == topology._ISOLATED_SAMPLE + 5
    assert len(stats["isolated_devices"]) == topology._ISOLATED_SAMPLE


def test_saving_a_position_only_touches_the_devices_that_moved(client, device_payload):
    """1台動かした保存で、他の機器の配置に触らないこと。

    以前は保存のたびに全機器と全配置を読み込んでいた。台数が増えるほど
    「動かすたびに固まる」状態になる。
    """
    moved = client.post(f"{API}/devices", json=device_payload(hostname="moved-01")).json()["id"]
    other = client.post(
        f"{API}/devices", json=device_payload(hostname="other-01", serial_number="X2")
    ).json()["id"]

    client.put(
        f"{API}/topology/positions",
        json={"layer": "physical", "positions": [{"device_id": other, "x": 111.0, "y": 222.0}]},
    )
    client.put(
        f"{API}/topology/positions",
        json={"layer": "physical", "positions": [{"device_id": moved, "x": 10.0, "y": 20.0}]},
    )

    graph = client.get(f"{API}/topology").json()
    where = {n["hostname"]: (n["x"], n["y"]) for n in graph["nodes"]}
    assert where["moved-01"] == (10.0, 20.0)
    assert where["other-01"] == (111.0, 222.0)  # 触られていない


def test_resetting_while_looking_at_one_site_keeps_the_other_sites(client, device_payload):
    """1拠点を見ている最中のリセットで、他の拠点の配置まで消さないこと。"""
    here = client.post(f"{API}/sites", json={"name": "見ている拠点"}).json()["id"]
    there = client.post(f"{API}/sites", json={"name": "別の拠点"}).json()["id"]
    a = client.post(f"{API}/devices", json=device_payload(hostname="here-01", site_id=here)).json()[
        "id"
    ]
    b = client.post(
        f"{API}/devices",
        json=device_payload(hostname="there-01", serial_number="X2", site_id=there),
    ).json()["id"]

    client.put(
        f"{API}/topology/positions",
        json={
            "layer": "physical",
            "positions": [
                {"device_id": a, "x": 1.0, "y": 1.0},
                {"device_id": b, "x": 500.0, "y": 500.0},
            ],
        },
    )

    client.delete(f"{API}/topology/positions", params={"site_id": here})

    graph = client.get(f"{API}/topology").json()
    where = {n["hostname"]: (n["x"], n["y"], n["placed"]) for n in graph["nodes"]}
    assert where["there-01"][:2] == (500.0, 500.0)  # 別の拠点は残る
    assert where["here-01"][2] is False  # 見ていた拠点だけ自動配置に戻る


def test_arranging_saves_the_tidy_layout(client, device_payload):
    """並べ直した位置は保存される（次に開いても同じ配置）。"""
    site = client.post(f"{API}/sites", json={"name": "整列する拠点"}).json()["id"]
    a = client.post(f"{API}/devices", json=device_payload(hostname="tidy-rt", site_id=site)).json()[
        "id"
    ]
    b = client.post(
        f"{API}/devices", json=device_payload(hostname="tidy-sw", serial_number="X2", site_id=site)
    ).json()["id"]
    client.post(
        f"{API}/topology/links",
        json={"a_device_id": a, "a_interface": "Gi0/0", "b_device_id": b, "b_interface": "Gi0/1"},
    )
    # わざとぐちゃぐちゃに置く
    client.put(
        f"{API}/topology/positions",
        json={
            "layer": "physical",
            "positions": [
                {"device_id": a, "x": 999.0, "y": 999.0},
                {"device_id": b, "x": -999.0, "y": -999.0},
            ],
        },
    )

    result = client.post(f"{API}/topology/positions/arrange", params={"site_id": site}).json()
    assert result["arranged"] == 2

    graph = client.get(f"{API}/topology", params={"site_id": site}).json()
    nodes = {n["hostname"]: n for n in graph["nodes"]}
    assert nodes["tidy-rt"]["placed"] is True  # 保存されている
    assert (nodes["tidy-rt"]["x"], nodes["tidy-rt"]["y"]) != (999.0, 999.0)
    # 上位が上、下位が下に並ぶ
    assert nodes["tidy-rt"]["y"] < nodes["tidy-sw"]["y"]
