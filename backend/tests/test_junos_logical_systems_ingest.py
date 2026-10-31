"""Junos の論理システムを、論理システムごとの機器として取り込む。

論理システムは自分の口・経路表を持つ別の機器として動く（出典: Juniper「Understanding Logical
Systems for Routers and Switches」）。1ファイルに入っていても、台帳では別の機器にする。
書き方は「Routing, Interfaces, and NAT for User Logical Systems」の set の設定例の形
（`set logical-systems LS1 interfaces ...`）。設計は docs/knowledge/decisions.md の
「Junos の論理システムを取り込むときの設計」。
"""

from sqlalchemy import select

from app.models import Device
from app.models.topology import ConfigSnapshot, Interface
from app.services import configparse
from tests.conftest import API

CONFIG = """set system host-name pe-01
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set routing-options static route 0.0.0.0/0 next-hop 10.0.0.2
set logical-systems LS1 interfaces ge-0/0/1 unit 0 family inet address 10.1.0.1/24
set logical-systems LS1 routing-options static route 10.10.0.0/16 next-hop 10.1.0.254
set logical-systems LS2 interfaces ge-0/0/2 unit 0 family inet address 10.2.0.1/24
set logical-systems LS2 routing-options static route 10.20.0.0/16 next-hop 10.2.0.254
"""


def _upload(client, **data):
    files = [("files", ("pe-01.txt", CONFIG.encode(), "text/plain"))]
    form = {"create_missing": "true", **data}
    return client.post(f"{API}/configs/upload", files=files, data=form)


def _device(db, hostname):
    return db.scalars(select(Device).where(Device.hostname == hostname)).first()


def _interfaces(db, device):
    rows = db.scalars(select(Interface).where(Interface.device_id == device.id)).all()
    return {row.name for row in rows}


def _routes(db, device):
    snapshot = db.scalars(
        select(ConfigSnapshot)
        .where(ConfigSnapshot.device_id == device.id)
        .order_by(ConfigSnapshot.id.desc())
    ).first()
    return {route.destination for route in configparse.parse(snapshot.content).routes}


def test_each_logical_system_becomes_its_own_device(client, db_session):
    response = _upload(client)
    assert response.status_code == 200, response.text

    main = _device(db_session, "pe-01")
    ls1 = _device(db_session, "pe-01_LS1")
    ls2 = _device(db_session, "pe-01_LS2")
    assert main and ls1 and ls2, [r["device_hostname"] for r in response.json()]

    # 口と経路は、それぞれの機器に分かれて載る。全体に論理システムの口は混ざらない
    assert "ge-0/0/0" in {name.split(".")[0] for name in _interfaces(db_session, main)}
    assert not {"ge-0/0/1", "ge-0/0/2"} & {n.split(".")[0] for n in _interfaces(db_session, main)}
    assert "ge-0/0/1" in {name.split(".")[0] for name in _interfaces(db_session, ls1)}
    assert "ge-0/0/2" in {name.split(".")[0] for name in _interfaces(db_session, ls2)}
    assert "0.0.0.0/0" in _routes(db_session, main)
    assert "10.10.0.0/16" in _routes(db_session, ls1)
    assert "10.20.0.0/16" in _routes(db_session, ls2)

    # 種別と拠点は全体の機器から写す（decisions.md の 2026-10-31 のデフォルト）
    for child in (ls1, ls2):
        assert child.device_type == main.device_type
        assert child.site_id == main.site_id


def test_a_named_device_is_not_split(client, db_session, device_payload):
    """機器を指定して取り込んだときは分けない。指定した1台と食い違うため。"""
    device_id = client.post(f"{API}/devices", json=device_payload(hostname="pe-01")).json()["id"]
    response = _upload(client, device_id=str(device_id))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert _device(db_session, "pe-01_LS1") is None
