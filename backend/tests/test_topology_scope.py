"""見たい範囲だけを返すこと。

全国展開すると拠点は数千・数万になる。そこで全体図を返す API は使えない。
実際に見るのは「自分の拠点」と「その上位（対向）まで」で、そこを軽く返せるか。

拠点だけに絞ると線が1本も出ず「どこにも繋がっていない」図になってしまうので、
既定では対向まで足す。**足した機器は、絞り込みの中にいる機器と区別が付くこと。**
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
