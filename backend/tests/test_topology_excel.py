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
