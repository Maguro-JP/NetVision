"""SNMP で取った値を読む。

読めない値を読めたことにしない、台帳との食い違いを黙らない、の2つを見る。
Config は「どう設定してあるか」、SNMP は「いまどうなっているか」なので、
食い違いこそが知りたいこと。
"""

from app.models import Device
from app.models.topology import Interface
from app.services import mib
from tests.conftest import API

WALK = """
SNMPv2-MIB::sysDescr.0 = STRING: Cisco IOS Software, C9300 Software (CAT9K_IOSXE), Version 17.09.04a
SNMPv2-MIB::sysName.0 = STRING: acc-01
SNMPv2-MIB::sysLocation.0 = STRING: HQ 3F RACK-2
IF-MIB::ifDescr.1 = STRING: GigabitEthernet1/0/1
IF-MIB::ifDescr.2 = STRING: GigabitEthernet1/0/2
IF-MIB::ifAdminStatus.1 = INTEGER: up(1)
IF-MIB::ifOperStatus.1 = INTEGER: up(1)
IF-MIB::ifAdminStatus.2 = INTEGER: up(1)
IF-MIB::ifOperStatus.2 = INTEGER: down(2)
IF-MIB::ifAlias.1 = STRING: to-core-01
IF-MIB::ifHighSpeed.1 = Gauge32: 1000
"""


def _device(db, **values) -> Device:
    device = Device(hostname="acc-01", vendor="Cisco", model="C9300-48P", **values)
    db.add(device)
    db.flush()
    return device


class TestReadingTheOutput:
    def test_symbolic_and_numeric_oids_land_in_the_same_place(self):
        """現場の出力は、名前の形も数字の形もある。"""
        assert mib.key_of("SNMPv2-MIB::sysName.0") == ("sysname", "0")
        assert mib.key_of(".1.3.6.1.2.1.1.5.0") == ("sysname", "0")

    def test_the_port_number_is_kept(self):
        assert mib.key_of("IF-MIB::ifOperStatus.10101") == ("ifoperstatus", "10101")
        assert mib.key_of(".1.3.6.1.2.1.2.2.1.8.10101") == ("ifoperstatus", "10101")

    def test_an_unknown_oid_is_not_guessed(self):
        key, _ = mib.key_of(".1.3.6.1.4.1.99999.1")
        assert key not in mib.MEANINGS

    def test_values_keep_their_meaning(self):
        found = {v.key: v for v in mib.parse(WALK)}
        assert found["sysname"].raw == "acc-01"
        assert found["sysname"].label == "機器の名前"


class TestWhatItFinds:
    def test_a_port_that_should_be_up_but_is_down_is_surfaced(self, db_session):
        _device(db_session)
        db_session.commit()

        report = mib.review(db_session, WALK)
        assert [p.name for p in report.down_ports] == ["GigabitEthernet1/0/2"]
        assert any("落ちている" in note for note in report.notes)

    def test_the_os_version_is_compared_with_the_ledger(self, db_session):
        _device(db_session, os_version="17.06.05")
        db_session.commit()

        report = mib.review(db_session, WALK)
        assert any("OS の版が違います" in text for text in report.differences)
        assert any("台帳のほうが古い" in text for text in report.differences)

    def test_a_swapped_box_is_surfaced(self, db_session):
        """製造番号が違えば、機器そのものが入れ替わっている。"""
        _device(db_session, serial_number="FCW1234ABCD")
        db_session.commit()

        report = mib.review(
            db_session,
            WALK + "\nENTITY-MIB::entPhysicalSerialNum.1 = STRING: FCW9999ZZZZ\n",
        )
        assert any("製造番号が違います" in text for text in report.differences)

    def test_a_description_that_drifted_is_surfaced(self, db_session):
        device = _device(db_session)
        db_session.add(
            Interface(
                device_id=device.id,
                name="GigabitEthernet1/0/1",
                normalized_name="gigabitethernet1/0/1",
                description="to-core-02",
            )
        )
        db_session.commit()

        report = mib.review(db_session, WALK)
        assert any("説明が違います" in text for text in report.differences)

    def test_a_host_that_is_not_in_the_ledger_says_so(self, db_session):
        report = mib.review(db_session, WALK)
        assert any("台帳にありません" in note for note in report.notes)
        assert report.differences == []

    def test_what_was_not_read_is_counted(self, db_session):
        report = mib.review(db_session, ".1.3.6.1.4.1.99999.1.0 = STRING: なにか\n")
        assert report.unknown == 1
        assert any("読めていません" in note for note in report.notes)

    def test_it_never_writes_to_the_ledger(self, db_session):
        _device(db_session, os_version="17.06.05")
        db_session.commit()

        mib.review(db_session, WALK)
        device = db_session.query(Device).filter_by(hostname="acc-01").one()
        assert device.os_version == "17.06.05"


def test_the_api_reads_what_was_pasted(db_session, client):
    _device(db_session)
    db_session.commit()

    response = client.post(f"{API}/mib", json={"text": WALK})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hostname"] == "acc-01"
    assert len(body["ports"]) == 2


def test_an_empty_body_is_refused(client):
    assert client.post(f"{API}/mib", json={"text": "   "}).status_code == 422
