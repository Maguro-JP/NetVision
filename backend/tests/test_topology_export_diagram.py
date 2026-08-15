"""資料に出す SVG は、画面の拠点図と同じであること。

全台を置いてから自拠点だけ残すと、箱が網の座標のまま千切れ、
数千ピクセルの空白と斜め線になる。それは構成図ではない。
"""

import re

from app.models.device import Device, DeviceType
from app.models.topology import Link
from app.services.topology import load_diagram
from app.services.topology_export import export_svg
from tests.test_topology_scope import build, build_center


def svg_width(svg: str) -> float:
    match = re.search(r'<svg[^>]*\bwidth="([\d.]+)"', svg)
    assert match, "SVG に width が無い"
    return float(match.group(1))


def has_diagonal(svg: str) -> bool:
    for match in re.finditer(r'<polyline points="([^"]+)"', svg):
        pts = [(float(p.split(",")[0]), float(p.split(",")[1])) for p in match.group(1).split(" ")]
        for a, b in zip(pts, pts[1:], strict=False):
            if abs(a[0] - b[0]) > 8 and abs(a[1] - b[1]) > 8:
                return True
    return False


def test_site_export_matches_the_screen_diagram(db_session):
    """書き出しは画面と同じ機器・同じ配置を使う。"""
    ids = build_center(db_session)
    screen = load_diagram(db_session, "physical", site_id=ids["branch"])
    svg = export_svg(db_session, "physical", site_id=ids["branch"], style="simple")

    names = {n.hostname for n in screen.nodes}
    for hostname in names:
        assert hostname in svg

    placed = {n.hostname: n for n in screen.nodes}
    assert placed["west-core-rt-01"].y < placed["ksi-br-rt-01"].y
    assert placed["kyoto-rt-01"].x > placed["ksi-br-rt-01"].x + 80
    assert not has_diagonal(svg), "線が斜め。縦横だけで通すこと"
    assert svg_width(svg) < 1800


def test_site_export_does_not_keep_world_coordinates(db_session):
    """網が広くても、拠点の SVG は間延びしない。

    書き出しが全台配置のまま自拠点を切ると、箱が端と端に分かれ、
    4000px 超の空白に水平線が走る。
    """
    ids = build(db_session)
    core = db_session.query(Device).filter(Device.hostname == "east-core-rt-01").one()
    prev = core
    for index in range(12):
        extra = Device(
            hostname=f"pad-{index:02d}",
            vendor="Cisco",
            model="C9300",
            device_type=DeviceType.SWITCH,
            site_id=core.site_id,
        )
        db_session.add(extra)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=prev.id,
                a_interface=f"Gi0/{index + 10}",
                b_device_id=extra.id,
                b_interface="Gi0/0",
            )
        )
        prev = extra
    db_session.commit()

    svg = export_svg(db_session, "physical", site_id=ids["osaka"], style="simple")
    assert "osaka-rt-01" in svg
    assert "east-core-rt-01" in svg
    assert "pad-00" not in svg
    assert svg_width(svg) < 1000, svg_width(svg)
    assert not has_diagonal(svg)
