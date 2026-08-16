"""見たい範囲だけを返すこと。

全国展開すると拠点は数千・数万になる。そこで全体図を返す API は使えない。
実際に見るのは「自分の拠点」と「その上位（対向）まで」で、そこを軽く返せるか。

拠点だけに絞ると線が1本も出ず「どこにも繋がっていない」図になってしまうので、
既定では対向まで足す。足した機器は、絞り込みの中にいる機器と区別が付くこと。
"""

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link
from app.services import topology
from tests.conftest import API


def build(db) -> dict[str, int]:
    tokyo = Site(name="東日本センター", address="東京都")
    osaka = Site(name="大阪府営業所", address="大阪府")
    kobe_site = Site(name="兵庫県営業所", address="兵庫県")
    db.add_all([tokyo, osaka, kobe_site])
    db.flush()

    center = Device(
        hostname="east-core-rt-01",
        vendor="Juniper",
        model="MX304",
        device_type=DeviceType.ROUTER,
        site_id=tokyo.id,
    )
    station_rt = Device(
        hostname="osaka-rt-01",
        vendor="NEC",
        model="IX2215",
        device_type=DeviceType.ROUTER,
        site_id=osaka.id,
    )
    station_sw = Device(
        hostname="osaka-sw-01",
        vendor="NEC",
        model="QX-S5528P",
        device_type=DeviceType.SWITCH,
        site_id=osaka.id,
    )
    # 上下がはっきりする程度の深さを持たせる。3台の一直線だと、
    # どちら側が上位かは形からは決まらない
    hq = Device(
        hostname="hq-rt-01",
        vendor="Juniper",
        model="MX304",
        device_type=DeviceType.ROUTER,
        site_id=tokyo.id,
    )
    kobe = Device(
        hostname="kobe-rt-01",
        vendor="NEC",
        model="IX2215",
        device_type=DeviceType.ROUTER,
        site_id=kobe_site.id,
    )
    db.add_all([center, station_rt, station_sw, hq, kobe])
    db.flush()
    db.add_all(
        [
            Link(
                a_device_id=hq.id,
                a_interface="ge-0/0/0",
                b_device_id=center.id,
                b_interface="ge-0/0/0",
            ),
            Link(
                a_device_id=kobe.id,
                a_interface="GigaEthernet0/0",
                b_device_id=center.id,
                b_interface="ge-0/0/2",
            ),
            Link(
                a_device_id=station_rt.id,
                a_interface="GigaEthernet0/0",
                b_device_id=center.id,
                b_interface="ge-0/0/1",
            ),
            Link(
                a_device_id=station_rt.id,
                a_interface="GigaEthernet0/1",
                b_device_id=station_sw.id,
                b_interface="GigabitEthernet1/0/25",
            ),
        ]
    )
    db.commit()
    return {"osaka": osaka.id, "tokyo": tokyo.id}


def test_site_scope_keeps_the_uplink_so_the_line_is_not_lost(db_session):
    """拠点で絞っても、上位（対向）は残ること。

    線が1本も出ない図は「どこにも繋がっていない」と読まれる。
    """
    ids = build(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["osaka"])

    names = {n.hostname for n in graph.nodes}
    assert names == {"osaka-rt-01", "osaka-sw-01", "east-core-rt-01"}
    assert len(graph.edges) == 2

    # 対向として足した機器は区別が付くこと
    outside = {n.hostname for n in graph.nodes if n.outside}
    assert outside == {"east-core-rt-01"}


def test_site_only_scope_drops_the_peer(db_session):
    ids = build(db_session)
    graph = topology.scope_graph(
        topology.get_graph(db_session), site_id=ids["osaka"], scope=topology.SCOPE_SITE
    )
    assert {n.hostname for n in graph.nodes} == {"osaka-rt-01", "osaka-sw-01"}
    assert len(graph.edges) == 1


def test_search_matches_site_name_not_only_hostname(db_session):
    """拠点名で探せること。機器名の命名規則を知らない人でも辿り着ける。"""
    build(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), q="大阪府")
    assert {n.hostname for n in graph.nodes if not n.outside} == {"osaka-rt-01", "osaka-sw-01"}


def test_no_filter_returns_everything(db_session):
    build(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session))
    assert len(graph.nodes) == 5
    assert all(not n.outside for n in graph.nodes)


def test_endpoint_accepts_the_filter(client, db_session):
    ids = build(db_session)
    response = client.get(f"{API}/topology", params={"site_id": ids["osaka"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {n["hostname"] for n in body["nodes"]} == {
        "osaka-rt-01",
        "osaka-sw-01",
        "east-core-rt-01",
    }
    assert [n["hostname"] for n in body["nodes"] if n["outside"]] == ["east-core-rt-01"]


def build_center(db) -> dict[str, int]:
    """センターが2台構成で、営業所が2つぶら下がっている形。"""
    center = Site(name="西日本センター", address="大阪府")
    branch = Site(name="関西支社", address="大阪府")
    office_a = Site(name="京都府営業所", address="京都府")
    office_b = Site(name="奈良県営業所", address="奈良県")
    db.add_all([center, branch, office_a, office_b])
    db.flush()

    def router(hostname: str, site: Site) -> Device:
        device = Device(
            hostname=hostname,
            vendor="Cisco",
            model="ISR4451-X",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db.add(device)
        return device

    core_a = router("west-core-rt-01", center)
    core_b = router("west-core-rt-02", center)
    branch_rt = router("ksi-br-rt-01", branch)
    kyoto = router("kyoto-rt-01", office_a)
    kyoto_sw = router("kyoto-rt-02", office_a)
    nara = router("nara-rt-01", office_b)
    db.flush()

    for a, b in (
        (branch_rt, core_a),
        (branch_rt, core_b),  # センターは2台とも繋がっている
        (kyoto, branch_rt),
        (kyoto_sw, branch_rt),  # 同じ営業所から2台繋がっている
        (nara, branch_rt),
        (core_a, core_b),  # 対向どうしの線
    ):
        db.add(Link(a_device_id=a.id, a_interface="Gi0/0", b_device_id=b.id, b_interface="Gi0/1"))
    db.commit()
    return {"branch": branch.id, "center": center.id}


def test_each_boundary_peer_is_kept_so_redundancy_stays_visible(db_session):
    """こちらと線がある対向は残す。折ると冗長の2台目が消える。"""
    ids = build_center(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["branch"])

    outside = sorted(n.hostname for n in graph.nodes if n.outside)
    assert "west-core-rt-01" in outside
    assert "west-core-rt-02" in outside
    assert "kyoto-rt-01" in outside
    assert "kyoto-rt-02" in outside
    assert "nara-rt-01" in outside


def test_boundary_peers_are_not_folded_into_a_count(db_session):
    """境界の対向は台数に畳まず、箱として出す。"""
    ids = build_center(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["branch"])

    folded = {n.hostname: n.hidden_peers for n in graph.nodes if n.outside}
    assert all(count == 0 for count in folded.values())
    cores = [n for n in graph.nodes if n.hostname.startswith("west-core-rt-")]
    assert len(cores) == 2
    assert abs(cores[0].y - cores[1].y) < 1
    assert abs(cores[0].x - cores[1].x) >= 200


def test_lines_between_two_peers_are_not_drawn(db_session):
    """対向どうしを結ぶ線は出さない。絞り込んだ範囲の外の話。"""
    ids = build_center(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["branch"])

    inside = {n.device_id for n in graph.nodes if not n.outside}
    assert all(e.source in inside or e.target in inside for e in graph.edges)


def test_the_kept_devices_are_laid_out_again_so_the_figure_is_not_spread_out(db_session):
    """絞ったら、その分だけで並べ直すこと。

    座標は網全体を前提に振ってあるので、そのまま出すと数台でも画面いっぱいに
    散らばって、線を目で追えなくなる。
    """
    ids = build(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["osaka"])

    width = max(n.x for n in graph.nodes) - min(n.x for n in graph.nodes)
    height = max(n.y for n in graph.nodes) - min(n.y for n in graph.nodes)
    # 3台なら、段2つ・横1つ分に収まる（詳細箱のすき間を見込む）
    assert width <= 600
    assert height <= 520


def test_a_saved_layout_is_not_rearranged(db_session):
    """人が動かして保存した配置は戻さない。直した意味が無くなる。"""
    ids = build(db_session)
    graph = topology.get_graph(db_session)
    moved = next(n for n in graph.nodes if n.hostname == "osaka-rt-01")
    moved.x, moved.y, moved.placed = 4321.0, 1234.0, True

    scoped = topology.scope_graph(graph, site_id=ids["osaka"])
    kept = next(n for n in scoped.nodes if n.hostname == "osaka-rt-01")
    assert (kept.x, kept.y) == (4321.0, 1234.0)


def test_a_placed_peer_does_not_block_rearranging_the_site(db_session):
    """対向がこの拠点の図で保存済みでも、自拠点の並べ直しは止まらないこと。

    対向の位置まで「保存済み」と見なすと、拠点を見ているときの最適化も
    リセットも効かなくなる。対向はこの図の脇へ寄せる対象なので、内側は並べ直す。
    この図で置いた対向は残す。
    """
    ids = build(db_session)
    graph = topology.get_graph(db_session)
    peer = next(n for n in graph.nodes if n.hostname == "east-core-rt-01")
    peer.x, peer.y, peer.placed = 9999.0, 9999.0, True
    local = next(n for n in graph.nodes if n.hostname == "osaka-rt-01")
    local.x, local.y = 50.0, 800.0  # わざと散らす。placed は False のまま

    scoped = topology.scope_graph(graph, site_id=ids["osaka"])
    kept = {n.hostname: n for n in scoped.nodes}

    assert kept["east-core-rt-01"].placed is True
    assert (kept["east-core-rt-01"].x, kept["east-core-rt-01"].y) == (9999.0, 9999.0)
    assert (kept["osaka-rt-01"].x, kept["osaka-rt-01"].y) != (50.0, 800.0)
    assert kept["osaka-rt-01"].y < kept["osaka-sw-01"].y


def test_many_peers_are_spread_out_instead_of_overlapping(db_session):
    """対向が多くても、線が重ならないように広げること。

    同じ段に並べると機器の間を線が横切って、どれがどれだか分からなくなる。
    """
    site = Site(name="関西支社", address="大阪府")
    db_session.add(site)
    db_session.flush()

    hub = Device(
        hostname="ksi-br-rt-01",
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    db_session.add(hub)
    db_session.flush()
    for index in range(10):
        leaf = Device(
            hostname=f"rt-{index:02d}",
            vendor="NEC",
            model="IX2215",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(leaf)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"Gi0/1/{index}",
                b_device_id=leaf.id,
                b_interface="Gi0/0",
            )
        )
    db_session.commit()

    graph = topology.scope_graph(topology.get_graph(db_session), site_id=site.id)
    placed = {n.hostname: (n.x, n.y) for n in graph.nodes}

    # 上位は上、配下は1段下
    assert placed["ksi-br-rt-01"][1] < placed["rt-00"][1]
    # 配下は同じ段に並び、重ならない
    row = sorted(x for name, (x, y) in placed.items() if name != "ksi-br-rt-01")
    assert len({y for name, (x, y) in placed.items() if name != "ksi-br-rt-01"}) == 1
    assert all(b - a >= 200 for a, b in zip(row, row[1:], strict=False))
    # 上位は配下の中央に来る
    assert row[0] < placed["ksi-br-rt-01"][0] < row[-1]


def test_peers_clear_a_wide_hub_box(db_session):
    """左右に結線が多い箱は高さが伸びる。対向は右端より右に置く。"""
    from app.services.topology import _BOX_WIDTH, _box_height, _box_width, _visual_right

    site = Site(name="拠点", address="")
    db_session.add(site)
    db_session.flush()
    hub = Device(
        hostname="hub-rt",
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    db_session.add(hub)
    db_session.flush()
    peer = Device(
        hostname="peer-rt",
        vendor="NEC",
        model="IX2215",
        device_type=DeviceType.ROUTER,
        site_id=site.id + 999,
    )
    db_session.add(peer)
    db_session.flush()
    for index in range(8):
        leaf = Device(
            hostname=f"leaf-{index:02d}",
            vendor="NEC",
            model="IX2215",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(leaf)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"Gi0/1/{index}",
                b_device_id=leaf.id,
                b_interface="Gi0/0",
            )
        )
    db_session.add(
        Link(
            a_device_id=hub.id,
            a_interface="Gi0/2/0",
            b_device_id=peer.id,
            b_interface="Gi0/0",
        )
    )
    db_session.commit()

    graph = topology.scope_graph(topology.get_graph(db_session), site_id=site.id)
    hub_node = next(n for n in graph.nodes if n.hostname == "hub-rt")
    peer_node = next(n for n in graph.nodes if n.hostname == "peer-rt")
    # 右辺に多いので幅は既定、高さが伸びる。対向は見た目の右端より右。
    box_right = _visual_right(hub_node.x, 0)
    assert peer_node.x > box_right + 100
    assert _box_width(0) == _BOX_WIDTH
    assert _box_height(9) > _box_height(3)


def test_horizontal_orientation_puts_the_upper_device_on_the_left(db_session):
    """左右に並べるときは、上位が左・配下が右。"""
    ids = build(db_session)
    graph = topology.scope_graph(
        topology.get_graph(db_session, orientation="horizontal"),
        site_id=ids["osaka"],
        orientation="horizontal",
    )
    placed = {n.hostname: (n.x, n.y) for n in graph.nodes}
    assert placed["east-core-rt-01"][0] < placed["osaka-rt-01"][0] < placed["osaka-sw-01"][0]


def test_children_are_placed_in_port_order(db_session):
    """配下は、上位機器のポート番号の順に並べること。

    順不同に並べると線が交差するし、図の並びと実機のポート番号の並びも
    食い違って読みにくい。
    """
    site = Site(name="関西支社", address="大阪府")
    db_session.add(site)
    db_session.flush()

    hub = Device(
        hostname="br-rt-01",
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    db_session.add(hub)
    db_session.flush()

    # わざと名前の順とポートの順を食い違わせる
    for port, hostname in ((1, "zzz-rt-01"), (2, "mmm-rt-01"), (10, "aaa-rt-01")):
        leaf = Device(
            hostname=hostname,
            vendor="NEC",
            model="IX2215",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(leaf)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"GigabitEthernet0/1/{port}",
                b_device_id=leaf.id,
                b_interface="GigaEthernet0/0",
            )
        )
    db_session.commit()

    graph = topology.scope_graph(topology.get_graph(db_session), site_id=site.id)
    placed = {n.hostname: n.x for n in graph.nodes}
    # ポート 1 → 2 → 10 の順に左から並ぶ（文字列順なら 1, 10, 2 になる）
    assert placed["zzz-rt-01"] < placed["mmm-rt-01"] < placed["aaa-rt-01"]


def test_unscoped_topology_is_rejected_when_too_wide(client, db_session):
    """全体図は台数が増えると画面が固まるので、API 側でも出さない。"""
    from app.models.device import Device, DeviceType
    from app.services.topology import UNSCOPED_GRAPH_LIMIT

    db_session.add_all(
        [
            Device(
                hostname=f"sw-{index:03d}",
                vendor="Cisco",
                model="C2960",
                device_type=DeviceType.SWITCH,
                serial_number=f"S{index}",
            )
            for index in range(UNSCOPED_GRAPH_LIMIT + 1)
        ]
    )
    db_session.commit()

    blocked = client.get(f"{API}/topology")
    assert blocked.status_code == 400
    assert "絞って" in blocked.json()["detail"]

    scoped = client.get(f"{API}/topology", params={"q": "sw-000"})
    assert scoped.status_code == 200
    assert len(scoped.json()["nodes"]) == 1
