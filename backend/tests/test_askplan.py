"""書いた文章から、増設の設計を起こす。

頼む側の頭の中には「本社の A の下に B を足したい」という文がある。
それをフォームの項目に分解できるのは台帳を知っている人だけで、
知らない人はそこで止まる。文章のまま受けられることを確かめる。
"""

import pytest

from app.models import Device, Site
from app.models.topology import ConfigSnapshot
from app.services import askplan
from tests.conftest import API

UPSTREAM = """hostname core-sw-01
!
vlan 10
 name OFFICE
!
interface GigabitEthernet1/0/2
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
"""


@pytest.fixture()
def ledger(db_session):
    site = Site(name="本社")
    other = Site(name="大阪支社")
    db_session.add_all([site, other])
    db_session.flush()
    core = Device(hostname="core-sw-01", vendor="Cisco", model="C9500-40X", site_id=site.id)
    osaka = Device(hostname="osaka-sw-01", vendor="Cisco", model="C9300", site_id=other.id)
    db_session.add_all([core, osaka])
    db_session.flush()
    db_session.add(
        ConfigSnapshot(
            device_id=core.id,
            hostname=core.hostname,
            filename="core.txt",
            content=UPSTREAM,
            sha256="a" * 64,
        )
    )
    db_session.commit()
    return {"site": site, "core": core, "osaka": osaka}


def test_it_reads_the_sentence_people_actually_write(db_session, ledger):
    result = askplan.understand(db_session, "本社の core-sw-01 の下に floor3-sw-01 を増設したい")
    assert result.ready
    assert result.reading.site == "本社"
    assert result.reading.upstream_device_id == ledger["core"].id
    assert result.reading.new_hostname == "floor3-sw-01"


def test_the_one_before_under_is_the_upstream(db_session, ledger):
    """「A の下に B」で、B を上位と読み違えない。"""
    result = askplan.understand(db_session, "core-sw-01 の下に osaka-sw-01 をつなぐ")
    assert result.reading.upstream_hostname == "core-sw-01"


def test_it_says_what_it_read_so_a_person_can_correct_it(db_session, ledger):
    result = askplan.understand(db_session, "本社の core-sw-01 の下に ap-sw-01 を追加")
    assert result.reading.notes
    assert any("core-sw-01" in note for note in result.reading.notes)


def test_a_purpose_word_becomes_the_vlan_name(db_session, ledger):
    result = askplan.understand(db_session, "本社の core-sw-01 の下に wifi-sw-01 を増設（無線用）")
    assert result.reading.purpose == "無線"


def test_a_missing_new_name_is_continued_from_the_upstream(db_session, ledger):
    result = askplan.understand(db_session, "本社の core-sw-01 の下にスイッチを増設したい")
    assert result.reading.new_hostname == "core-sw-02"
    assert any("続けて" in note for note in result.reading.notes)


def test_a_device_that_is_not_in_the_ledger_is_not_guessed(db_session, ledger):
    """「たぶんこの機器だろう」で別の機器の設計を出すのがいちばん困る。"""
    result = askplan.understand(db_session, "そのへんのスイッチの下に1台足したい")
    assert not result.ready
    assert any("台帳にある機器の名前" in q for q in result.questions)


def test_two_possible_devices_are_offered_instead_of_picking_one(db_session, ledger):
    result = askplan.understand(db_session, "core-sw-01 と osaka-sw-01 を増やしたい")
    assert not result.ready
    assert {c["hostname"] for c in result.upstream_choices} == {
        "core-sw-01",
        "osaka-sw-01",
    }


def test_the_site_narrows_down_a_shared_name(db_session):
    """同じ名前の機器が拠点ごとにあるのは普通のこと。"""
    tokyo = Site(name="東京")
    osaka = Site(name="大阪")
    db_session.add_all([tokyo, osaka])
    db_session.flush()
    db_session.add_all(
        [
            Device(hostname="sw-01", site_id=tokyo.id, vendor="Cisco", model="C9300"),
            Device(hostname="sw-02", site_id=osaka.id, vendor="Cisco", model="C9300"),
        ]
    )
    db_session.commit()

    result = askplan.understand(db_session, "大阪の sw-02 の下に sw-03 を増設")
    assert result.reading.site == "大阪"
    assert result.reading.upstream_hostname == "sw-02"


def test_an_empty_request_asks_for_one(db_session):
    result = askplan.understand(db_session, "   ")
    assert any("何をしたいか" in q for q in result.questions)


# ---------------------------------------------------------------------------
# API：文章から設計まで
# ---------------------------------------------------------------------------


def test_the_api_goes_from_a_sentence_to_a_finished_design(client, ledger):
    res = client.post(
        f"{API}/ask/addition",
        json={"text": "本社の core-sw-01 の下に floor3-sw-01 を増設したい"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["questions"] == []
    assert body["reading"]["upstream_hostname"] == "core-sw-01"

    plan = body["plan"]
    assert plan["vlan_id"] == 20
    assert plan["subnet"] == "10.10.20.0/24"
    assert "vlan 20" in plan["upstream_config"]
    assert "vlan 20" in plan["device_config"]
    assert plan["decisions"]


def test_the_api_returns_the_reading_even_when_it_cannot_finish(client, ledger):
    res = client.post(f"{API}/ask/addition", json={"text": "なんとかしてほしい"})
    body = res.json()
    assert body["plan"] is None
    assert body["questions"]


def test_a_person_can_correct_what_it_read(client, ledger):
    """読み違えたまま進まれるより、直せるほうが早い。"""
    res = client.post(
        f"{API}/ask/addition",
        json={
            "text": "1台足したい",
            "upstream_device_id": ledger["core"].id,
            "new_hostname": "floor3-sw-01",
            "purpose": "事務所",
        },
    )
    body = res.json()
    assert body["plan"]["hostname"] == "floor3-sw-01"
    assert body["plan"]["vlan_name"] == "VLAN20"


def test_correcting_with_an_unknown_device_is_refused(client, ledger):
    res = client.post(
        f"{API}/ask/addition", json={"text": "1台足したい", "upstream_device_id": 9999}
    )
    assert res.status_code == 404
