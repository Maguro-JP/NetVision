"""構成図を Excel のセルに描けること。

SVG は貼るだけなら良いが、手元で直せない。現場では機器を1台書き足す・
注記を入れるといった直しがその場で要るので、罫線で描いた図も出す。

見るのは「図に見えるか」——箱があり、線が引かれ、上位が左にあるか。
"""

import io

from openpyxl import load_workbook

from app.models.device import Device, DeviceType
from app.models.site import Site
from app.models.topology import Link
from app.services import topology_excel
from tests.conftest import API


def build(db) -> int:
    site = Site(name="大阪府営業所", address="大阪府")
    db.add(site)
    db.flush()
    rt = Device(
        hostname="osaka-rt-01",
        vendor="NEC",
        model="IX2215",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    sw = Device(
        hostname="osaka-sw-01",
        vendor="NEC",
        model="QX-S5528P",
        device_type=DeviceType.SWITCH,
        site_id=site.id,
    )
    db.add_all([rt, sw])
    db.flush()
    db.add(
        Link(
            a_device_id=rt.id,
            a_interface="GigaEthernet0/1",
            b_device_id=sw.id,
            b_interface="GigabitEthernet1/0/25",
        )
    )
    db.commit()
    return site.id


def sheet_of(db, **kwargs):
    return load_workbook(io.BytesIO(topology_excel.to_xlsx(db, **kwargs)))


def test_devices_become_boxes_with_borders(db_session):
    build(db_session)
    sheet = sheet_of(db_session)["構成図"]

    boxes = {
        str(cell.value).split("\n")[0]
        for row in sheet.iter_rows()
        for cell in row
        if cell.value and "\n" in str(cell.value)
    }
    assert {"osaka-rt-01", "osaka-sw-01"} <= boxes

    # 箱は罫線で囲まれていること（値だけでは図にならない）
    named = [
        cell
        for row in sheet.iter_rows()
        for cell in row
        if cell.value and str(cell.value).startswith("osaka-rt-01")
    ]
    assert named and named[0].border.top.style


def test_upper_device_is_placed_to_the_left(db_session):
    """上位を左、下位を右。ここが逆だと図として読めない。"""
    build(db_session)
    sheet = sheet_of(db_session)["構成図"]

    where = {}
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value and "\n" in str(cell.value):
                where[str(cell.value).split("\n")[0]] = cell.column
    assert where["osaka-rt-01"] < where["osaka-sw-01"]


def test_a_line_is_drawn_between_them(db_session):
    """箱と箱の間に罫線が引かれていること。"""
    build(db_session)
    sheet = sheet_of(db_session)["構成図"]

    drawn = sum(
        1
        for row in sheet.iter_rows()
        for cell in row
        if not cell.value and (cell.border.top.style or cell.border.left.style)
    )
    assert drawn > 0, "結線の罫線が1本も引かれていない"


def test_tables_are_in_the_same_file(db_session):
    """図と表を別ファイルにしない。配るのは1つで済ませたい。"""
    build(db_session)
    assert sheet_of(db_session).sheetnames == ["構成図", "結線表", "ポート一覧"]


def test_site_filter_keeps_the_uplink(db_session):
    site_id = build(db_session)
    sheet = sheet_of(db_session, site_id=site_id)["構成図"]
    values = [str(c.value) for row in sheet.iter_rows() for c in row if c.value]
    assert any("osaka-rt-01" in v for v in values)


def test_endpoint(client):
    response = client.get(f"{API}/topology/workbook.xlsx")
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK"


def test_the_upper_device_is_decided_by_the_whole_network_not_by_link_count(db_session):
    """上下を「繋がっている本数」で決めないこと。

    支社ルータは配下の拠点が多いぶん本数が多い。本数で並べると、
    センターより支社が上に来てしまう。
    """
    from app.services import topology
    from app.services.topology_excel import hierarchy_rank

    site = Site(name="関西支社", address="大阪府")
    db_session.add(site)
    db_session.flush()

    def router(hostname: str) -> Device:
        device = Device(
            hostname=hostname,
            vendor="Cisco",
            model="ISR4451-X",
            device_type=DeviceType.ROUTER,
            site_id=site.id,
        )
        db_session.add(device)
        return device

    core = router("center-rt-01")
    branch = router("branch-rt-01")
    stations = [router(f"station-{i}-rt-01") for i in range(4)]
    upstream = router("hq-rt-01")
    db_session.flush()

    pairs = [(upstream, core), (core, branch)] + [(branch, s) for s in stations]
    for a, b in pairs:
        db_session.add(
            Link(a_device_id=a.id, a_interface="Gi0/0", b_device_id=b.id, b_interface="Gi0/1")
        )
    db_session.commit()

    rank = hierarchy_rank(topology.get_graph(db_session))
    # 本数は branch(5) > core(2) だが、上位は core 側
    assert rank[core.id] < rank[branch.id]
    assert rank[branch.id] < rank[stations[0].id]


def test_both_ends_of_a_link_show_their_port_on_separate_rows(db_session):
    """1本の線に、上位側と下位側のポート名が両方出ること。

    同じ行に書くと罫線と重なって読めなくなるので、上下の行に分ける。
    上位機器に配下が何台あっても、線ごとにその機器のポートが出る。
    """
    site = Site(name="関西支社", address="大阪府")
    db_session.add(site)
    db_session.flush()

    parent = Device(
        hostname="br-rt-01",
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        site_id=site.id,
    )
    db_session.add(parent)
    db_session.flush()
    for index in range(3):
        child = Device(
            hostname=f"sw-{index:02d}",
            vendor="Cisco",
            model="C9300-48P",
            device_type=DeviceType.SWITCH,
            site_id=site.id,
        )
        db_session.add(child)
        db_session.flush()
        db_session.add(
            Link(
                a_device_id=parent.id,
                a_interface=f"GigabitEthernet0/1/{index}",
                b_device_id=child.id,
                b_interface="GigabitEthernet1/0/1",
            )
        )
    db_session.commit()

    sheet = sheet_of(db_session)["構成図"]
    cells = {
        (cell.row, cell.column): str(cell.value)
        for row in sheet.iter_rows()
        for cell in row
        if cell.value
    }

    # 上位側のポートは、配下の数だけ出る（1本しか出ないのは取りこぼし）
    downlinks = [v for v in cells.values() if v.startswith("GigabitEthernet0/1/")]
    assert sorted(downlinks) == [
        "GigabitEthernet0/1/0",
        "GigabitEthernet0/1/1",
        "GigabitEthernet0/1/2",
    ]

    # 上位側と下位側は別の行にある
    for (row, column), value in cells.items():
        if value.startswith("GigabitEthernet0/1/"):
            assert (row + 1, column) in cells or any(
                r == row + 1 and str(v).startswith("GigabitEthernet1/0/")
                for (r, _), v in cells.items()
            )


def test_the_sheet_leaves_a_margin_at_the_top_left(db_session):
    """見出しは B2 から。1台目の箱の左にも余白を空ける。"""
    build(db_session)
    sheet = sheet_of(db_session)["構成図"]

    assert sheet["A1"].value is None
    assert sheet["B2"].value == "構成図"

    boxes = [
        cell.column
        for row in sheet.iter_rows()
        for cell in row
        if cell.value and "\n" in str(cell.value)
    ]
    # 一番左の箱の手前に、上りポート名を書ける幅がある
    assert min(boxes) >= 4
