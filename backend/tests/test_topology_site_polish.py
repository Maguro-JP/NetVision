"""拠点図を、線まで引いてみてから置き直すこと。

置き方だけを見て決めていたときは、条件を全部満たしていても線が読めない図が
残った。ここで見るのは、置き方の候補を実際に描いて比べているかどうか。
"""

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link
from app.services import topology, topology_quality, topology_site
from app.services.topology_export import render_svg


def _branch_with_offices(db_session, offices: int) -> int:
    """センター2台の下に支社1台、その配下に営業所が並ぶ形を作る。"""
    center = Site(name="試験センター", address="大阪府")
    branch = Site(name="試験支社", address="愛知県")
    db_session.add_all([center, branch])
    db_session.flush()

    def router(hostname: str, site: Site, model: str = "ISR4451-X") -> Device:
        device = Device(
            hostname=hostname,
            vendor="Cisco",
            model=model,
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(device)
        return device

    cores = [router(f"try-core-rt-{i:02d}", center, "MX304") for i in (1, 2)]
    hub = router("try-br-rt-01", branch)
    db_session.flush()
    db_session.add(
        Link(
            a_device_id=cores[0].id,
            a_interface="ge-0/0/9",
            b_device_id=cores[1].id,
            b_interface="ge-0/0/9",
        )
    )
    for index, core in enumerate(cores):
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"Gi0/0/{index}",
                b_device_id=core.id,
                b_interface="ge-0/0/0",
            )
        )
    for index in range(offices):
        site = Site(name=f"試験営業所{index}", address="日本")
        db_session.add(site)
        db_session.flush()
        office = router(f"try-of{index:02d}-rt-01", site, "ISR1111")
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"Gi0/1/{index}",
                b_device_id=office.id,
                b_interface="GE0/0",
            )
        )
    db_session.commit()
    return branch.id


def _diagram(db_session, site_id: int):
    return topology.load_diagram(db_session, site_id=site_id)


def test_a_site_with_many_peers_is_not_left_as_a_tall_strip(db_session):
    """配下が多いと1列では縦に伸びる。紙にも画面にも収まらない。"""
    site_id = _branch_with_offices(db_session, 14)
    graph = _diagram(db_session, site_id)

    ratio = topology_site._ratio(graph)
    assert 0.9 <= ratio <= 3.0, f"縦横比 {ratio:.2f}"


def test_the_chosen_placement_is_one_that_draws_cleanly(db_session):
    """選んだ置き方で、線の側の条件まで通ること。"""
    site_id = _branch_with_offices(db_session, 14)
    graph = _diagram(db_session, site_id)

    svg = render_svg(graph, title="試験支社 物理構成", style="simple")
    findings = topology_quality.evaluate_svg(svg, graph) + topology_quality.evaluate_site_diagram(
        graph
    )
    assert topology_quality.is_acceptable(findings), [f.detail for f in findings if not f.ok]


def test_it_tries_more_than_one_placement(db_session):
    """1つしか作らないなら、選んでいることにならない。"""
    site_id = _branch_with_offices(db_session, 14)
    graph = _diagram(db_session, site_id)

    choice = topology_site.polish(graph)
    assert choice.tried >= 2
    assert choice.crossings == 0


def test_the_same_ledger_gives_the_same_diagram(db_session):
    """開くたびに置き場所が変わる図は覚えられない。"""
    site_id = _branch_with_offices(db_session, 9)
    first = [(n.hostname, n.x, n.y) for n in _diagram(db_session, site_id).nodes]
    second = [(n.hostname, n.x, n.y) for n in _diagram(db_session, site_id).nodes]
    assert first == second


def test_a_placement_a_person_made_is_left_alone(db_session):
    """置いた位置は意思。こちらの都合で動かさない。"""
    site_id = _branch_with_offices(db_session, 9)
    graph = _diagram(db_session, site_id)
    for node in graph.nodes:
        node.placed = True
    before = [(n.hostname, n.x, n.y) for n in graph.nodes]

    choice = topology_site.polish(graph)
    assert [(n.hostname, n.x, n.y) for n in graph.nodes] == before
    assert "人が置いた" in choice.name


def test_a_small_site_is_left_as_it_is(db_session):
    """対向が1台の営業所まで作り直しても、変わるものが無い。"""
    site_id = _branch_with_offices(db_session, 1)
    graph = _diagram(db_session, site_id)
    outside = [n for n in graph.nodes if n.outside]
    assert len(outside) >= 2  # 支社にはセンター2台と営業所1台が対向として出る

    choice = topology_site.polish(graph)
    assert choice.name in ("いまの置き方", "対向が少ないので触らない")
