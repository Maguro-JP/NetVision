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
    db.add_all([tokyo, osaka])
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
    db.add_all([center, station_rt, station_sw])
    db.flush()
    db.add_all(
        [
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
    assert len(graph.nodes) == 3
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


def test_each_peer_site_is_represented_by_one_device(db_session):
    """対向は相手の拠点ごとに1台だけにすること。

    支社を見たいのに、営業所の中まで2台3台と出てくると、肝心の支社が読めない。
    """
    ids = build_center(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["branch"])

    outside = sorted(n.hostname for n in graph.nodes if n.outside)
    # 京都府営業所は2台繋がっているが、出るのは1台
    assert outside == ["kyoto-rt-01", "nara-rt-01", "west-core-rt-01"]


def test_the_devices_folded_away_are_counted_not_hidden_silently(db_session):
    """まとめた分は台数として出す。黙って消すと「1台しかない」と読まれる。"""
    ids = build_center(db_session)
    graph = topology.scope_graph(topology.get_graph(db_session), site_id=ids["branch"])

    folded = {n.hostname: n.hidden_peers for n in graph.nodes if n.outside}
    assert folded["kyoto-rt-01"] == 1
    assert folded["west-core-rt-01"] == 1
    assert folded["nara-rt-01"] == 0


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
    # 3台なら、段2つ・横1つ分に収まる
    assert width <= 600
    assert height <= 400


def test_a_saved_layout_is_not_rearranged(db_session):
    """人が動かして保存した配置は戻さない。直した意味が無くなる。"""
    ids = build(db_session)
    graph = topology.get_graph(db_session)
    moved = next(n for n in graph.nodes if n.hostname == "osaka-rt-01")
    moved.x, moved.y, moved.placed = 4321.0, 1234.0, True

    scoped = topology.scope_graph(graph, site_id=ids["osaka"])
    kept = next(n for n in scoped.nodes if n.hostname == "osaka-rt-01")
    assert (kept.x, kept.y) == (4321.0, 1234.0)


def test_a_wide_tier_is_wrapped(db_session):
    """同じ段に並びすぎたら折り返すこと。横に伸びると画面に収まらない。"""
    site = Site(name="関西支社", address="大阪府")
    other = Site(name="京都府営業所", address="京都府")
    db_session.add_all([site, other])
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
    rows: dict[float, int] = {}
    for node in graph.nodes:
        rows[round(node.y)] = rows.get(round(node.y), 0) + 1
    assert len(rows) > 1
    assert max(rows.values()) <= 6
