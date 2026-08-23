"""構成図のパターンを全部通す。

支社、本社、営業所、冗長コア、配下が多い図、左右配置、物理と論理の分離。
どれか一つでも資料として読めなければ落ちる。
"""

from pathlib import Path

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link
from app.services.topology import _visual_right, get_graph, load_diagram, scope_graph
from app.services.topology_export import render_svg
from app.services.topology_quality import (
    evaluate_site_diagram,
    evaluate_svg,
    is_acceptable,
    score_diagram,
)
from tests.test_topology_layers import _device, _link, _port
from tests.test_topology_scope import build, build_center

PATTERNS = Path(__file__).resolve().parents[2] / "docs" / "topology-samples" / "patterns"


def _write(name: str, svg: str) -> None:
    PATTERNS.mkdir(parents=True, exist_ok=True)
    (PATTERNS / name).write_text(svg, encoding="utf-8")


def _ok(graph, title: str, filename: str) -> None:
    findings = evaluate_site_diagram(graph)
    failed = [item.detail for item in findings if not item.ok]
    assert is_acceptable(findings), f"{title}: {failed}"
    scored = None
    for style in ("simple", "detail"):
        svg = render_svg(graph, title=title, style=style)
        svg_findings = evaluate_svg(svg, graph)
        failed_svg = [item.detail for item in svg_findings if not item.ok]
        assert is_acceptable(svg_findings), f"{title} SVG({style}): {failed_svg}"
        if style == "simple":
            scored = score_diagram(graph, svg)
            assert scored.total > 100, f"{title} {scored.total}点 {scored.parts}"
            _write(filename, svg)
            lines = [f"{title}\t{scored.total}"]
            lines.extend(f"  {name}\t{value:.1f}" for name, value in scored.parts.items())
            _write(filename.replace(".svg", ".score.txt"), "\n".join(lines) + "\n")
        else:
            # 画面の既定は詳細。詳細も同じ合格線を通す
            _write(filename.replace(".svg", "-detail.svg"), svg)
    return scored


def test_branch_with_redundant_cores_and_offices(db_session):
    ids = build_center(db_session)
    graph = scope_graph(get_graph(db_session), site_id=ids["branch"])
    _ok(graph, "支社 物理構成", "branch.svg")
    cores = [n for n in graph.nodes if n.hostname.startswith("west-core-rt-")]
    assert len(cores) == 2
    assert abs(cores[0].y - cores[1].y) < 1


def test_hq_puts_edge_above_side_by_side_cores(db_session):
    hq = Site(name="本社データセンター", address="東京都")
    east = Site(name="東日本センター", address="東京都")
    west = Site(name="西日本センター", address="大阪府")
    db_session.add_all([hq, east, west])
    db_session.flush()

    def router(hostname: str, site: Site, model: str = "MX204") -> Device:
        device = Device(
            hostname=hostname,
            vendor="Juniper",
            model=model,
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(device)
        return device

    edge = router("edge-rt-01", hq)
    fw = Device(
        hostname="fw-01",
        vendor="Fortinet",
        model="FortiGate-100F",
        device_type=DeviceType.FIREWALL,
        site_id=hq.id,
    )
    core_a = Device(
        hostname="core-sw-01",
        vendor="Cisco",
        model="C9500",
        device_type=DeviceType.SWITCH,
        site_id=hq.id,
    )
    core_b = Device(
        hostname="core-sw-02",
        vendor="Cisco",
        model="C9500",
        device_type=DeviceType.SWITCH,
        site_id=hq.id,
    )
    acc = Device(
        hostname="acc-sw-01",
        vendor="Cisco",
        model="C2960",
        device_type=DeviceType.SWITCH,
        site_id=hq.id,
    )
    east_rt = router("east-core-rt-01", east, "MX304")
    west_rt = router("west-core-rt-01", west, "MX304")
    db_session.add_all([fw, core_a, core_b, acc])
    db_session.flush()
    for a, a_if, b, b_if in (
        (edge, "ge-0/0/0", fw, "port1"),
        (fw, "port2", core_a, "Gi1/0/24"),
        (fw, "port3", core_b, "Gi1/0/23"),
        (core_a, "Te1/1/1", core_b, "Te1/1/1"),
        (core_a, "Te1/1/2", core_b, "Te1/1/2"),
        (core_a, "Gi1/0/1", acc, "Gi0/1"),
        (edge, "ge-0/0/1", east_rt, "ge-0/0/0"),
        (edge, "ge-0/0/2", west_rt, "ge-0/0/0"),
    ):
        db_session.add(Link(a_device_id=a.id, a_interface=a_if, b_device_id=b.id, b_interface=b_if))
    db_session.commit()

    graph = scope_graph(get_graph(db_session), site_id=hq.id)
    _ok(graph, "本社 物理構成", "hq.svg")
    placed = {n.hostname: n for n in graph.nodes}
    assert placed["edge-rt-01"].y < placed["fw-01"].y < placed["core-sw-01"].y
    assert abs(placed["core-sw-01"].y - placed["core-sw-02"].y) < 1
    assert abs(placed["core-sw-01"].x - placed["core-sw-02"].x) >= 200


def test_office_keeps_the_uplink_above(db_session):
    ids = build(db_session)
    graph = scope_graph(get_graph(db_session), site_id=ids["osaka"])
    _ok(graph, "営業所 物理構成", "office.svg")
    placed = {n.hostname: n for n in graph.nodes}
    assert placed["east-core-rt-01"].y < placed["osaka-rt-01"].y < placed["osaka-sw-01"].y


def test_many_offices_stay_in_a_right_column(db_session):
    branch = Site(name="関西支社", address="大阪府")
    center = Site(name="西日本センター", address="大阪府")
    db_session.add_all([branch, center])
    db_session.flush()
    hub = Device(
        hostname="ksi-br-rt-01",
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        site_id=branch.id,
    )
    core_a = Device(
        hostname="west-core-rt-01",
        vendor="Juniper",
        model="MX304",
        device_type=DeviceType.ROUTER,
        site_id=center.id,
    )
    core_b = Device(
        hostname="west-core-rt-02",
        vendor="Juniper",
        model="MX304",
        device_type=DeviceType.ROUTER,
        site_id=center.id,
    )
    db_session.add_all([hub, core_a, core_b])
    db_session.flush()
    db_session.add(
        Link(
            a_device_id=hub.id,
            a_interface="Gi0/0/0",
            b_device_id=core_a.id,
            b_interface="ge-0/0/1",
        )
    )
    db_session.add(
        Link(
            a_device_id=hub.id,
            a_interface="Gi0/0/2",
            b_device_id=core_b.id,
            b_interface="ge-0/0/1",
        )
    )
    db_session.add(
        Link(
            a_device_id=core_a.id,
            a_interface="ge-0/1/0",
            b_device_id=core_b.id,
            b_interface="ge-0/1/0",
        )
    )
    for index, name in enumerate(("shiga", "kyoto", "osaka", "hyogo", "nara", "wakayama"), start=1):
        office = Site(name=f"{name}営業所", address=name)
        db_session.add(office)
        db_session.flush()
        rt = Device(
            hostname=f"{name}-rt-01",
            vendor="NEC",
            model="IX2215",
            device_type=DeviceType.ROUTER,
            site_id=office.id,
        )
        db_session.add(rt)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=hub.id,
                a_interface=f"Gi0/1/{index}",
                b_device_id=rt.id,
                b_interface="GE0/0",
            )
        )
    db_session.commit()

    graph = scope_graph(get_graph(db_session), site_id=branch.id)
    _ok(graph, "支社 配下6拠点", "branch-six-offices.svg")
    offices = [n for n in graph.nodes if n.outside and n.peer_kind == "down"]
    assert len(offices) == 6
    xs = {round(n.x) for n in offices}
    assert len(xs) == 1
    hub = next(n for n in graph.nodes if n.hostname == "ksi-br-rt-01")
    # 左右から出すため COLUMN(400) 以上離す。ポート名用のすき間で通り道より少し広い
    assert min(n.x for n in offices) - _visual_right(hub.x, 0) < 360
    assert min(n.x for n in offices) - hub.x >= 400


def test_horizontal_orientation_is_still_readable(db_session):
    ids = build(db_session)
    graph = scope_graph(
        get_graph(db_session, orientation="horizontal"),
        site_id=ids["osaka"],
        orientation="horizontal",
    )
    _ok(graph, "営業所 左右配置", "office-horizontal.svg")
    placed = {n.hostname: n for n in graph.nodes}
    assert placed["east-core-rt-01"].x < placed["osaka-rt-01"].x < placed["osaka-sw-01"].x
    # 箱幅より狭い間隔だと食い込む（左右配置の段ピッチ退行防止）
    assert placed["osaka-sw-01"].x - placed["osaka-rt-01"].x >= 190
    assert placed["osaka-rt-01"].x - placed["east-core-rt-01"].x >= 190


def test_physical_and_l2_are_different_sheets(db_session):
    core = _device(db_session, "core-sw", DeviceType.SWITCH)
    acc = _device(db_session, "acc-sw", DeviceType.SWITCH)
    edge = _device(db_session, "edge-rt", DeviceType.ROUTER)
    branch = _device(db_session, "br-rt", DeviceType.ROUTER)
    _link(db_session, core, "Gi1/0/1", acc, "Gi0/1")
    _link(db_session, edge, "Gi0/0", branch, "Gi0/0")
    _port(db_session, core, "Gi1/0/1", mode="trunk", native_vlan=999, trunk_vlans="10,20")
    _port(db_session, acc, "Gi0/1", mode="trunk", native_vlan=999, trunk_vlans="10,20")
    _port(db_session, edge, "Gi0/0")
    _port(db_session, branch, "Gi0/0")
    db_session.commit()

    physical = get_graph(db_session, "physical")
    l2 = get_graph(db_session, "l2")
    assert {e.id for e in physical.edges} != {e.id for e in l2.edges}
    _write("physical.svg", render_svg(physical, title="物理構成", style="simple"))
    _write("l2.svg", render_svg(l2, title="L2 構成", style="simple"))
    assert "edge-rt" in PATTERNS.joinpath("physical.svg").read_text(encoding="utf-8")
    assert "edge-rt" not in PATTERNS.joinpath("l2.svg").read_text(encoding="utf-8")


def test_looking_again_does_not_move_saved_boxes(db_session):
    ids = build(db_session)
    first = load_diagram(db_session, "physical", site_id=ids["osaka"])
    moved = next(n for n in first.nodes if n.hostname == "osaka-rt-01")
    from app.services.topology import save_positions

    save_positions(
        db_session,
        "physical",
        [(moved.device_id, 4321.0, 1234.0)],
        site_id=ids["osaka"],
        pinned=True,
    )
    again = load_diagram(db_session, "physical", site_id=ids["osaka"])
    kept = next(n for n in again.nodes if n.hostname == "osaka-rt-01")
    assert (kept.x, kept.y) == (4321.0, 1234.0)


def test_center_with_dual_homed_branches(db_session):
    """センターから見た図。支社は2台のコアの両方につながる。

    この形が長く汚かった。コアの右にいる支社へ下から線を出していたため、
    線が箱の中を通り、切れ目の無い交差が残っていた。
    """
    center = Site(name="東日本センター", address="東京都")
    hq = Site(name="本社データセンター", address="東京都")
    db_session.add_all([center, hq])
    db_session.flush()

    def router(hostname: str, site: Site) -> Device:
        device = Device(
            hostname=hostname,
            vendor="Juniper",
            model="MX304",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(device)
        return device

    core_a = router("east-core-rt-01", center)
    core_b = router("east-core-rt-02", center)
    edge = router("edge-rt-01", hq)
    branches = []
    for index, name in enumerate(("hkd", "thk", "knt"), start=1):
        site = Site(name=f"{name}支社", address="日本")
        db_session.add(site)
        db_session.flush()
        branches.append((index, router(f"{name}-br-rt-01", site)))
    db_session.flush()

    links = [
        (core_a, "ge-0/0/0", edge, "ge-0/0/1"),
        (core_a, "ge-0/1/0", core_b, "ge-0/1/0"),
    ]
    for index, branch in branches:
        links.append((branch, "Gi0/0/0", core_a, f"ge-0/0/{index}"))
        links.append((branch, "Gi0/0/2", core_b, f"ge-0/0/{index}"))
    for a, a_if, b, b_if in links:
        db_session.add(Link(a_device_id=a.id, a_interface=a_if, b_device_id=b.id, b_interface=b_if))
    db_session.commit()

    graph = scope_graph(get_graph(db_session), site_id=center.id)
    _ok(graph, "センター 物理構成", "center.svg")

    svg = render_svg(graph, title="センター 物理構成", style="simple")
    placed = {n.hostname: n for n in graph.nodes}
    # 2台のコアは横並び。縦に積むと主系・予備に見える
    assert abs(placed["east-core-rt-01"].y - placed["east-core-rt-02"].y) < 1
    # 右にいる支社へは、下ではなく横から出す
    assert "Gi0/0/0" in svg and "Gi0/0/2" in svg
