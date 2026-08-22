"""渡された Config を、台帳に入れずにその場で見る。

「これ、どこか変じゃないか」を先に知りたい場面のほうが多い。試験用に
書いたもの、業者から届いたもの、自分で書いてみたもの。
"""

import io

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import configcheck
from app.services.l3review import HIGH, LOW, MEDIUM
from tests.conftest import API

MESSY = """hostname test-sw-01
!
vlan 10
 name OFFICE
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
!
interface Vlan20
 ip address 10.10.20.1 255.255.255.0
 shutdown
!
ip http server
snmp-server community public RO
enable password cisco
!
ip route 0.0.0.0 0.0.0.0 192.168.99.254
!
line vty 0 15
 transport input telnet
"""

TIDY = """hostname clean-sw-01
!
vlan 10
 name OFFICE
!
interface Vlan10
 description office
 ip address 10.10.10.1 255.255.255.0
!
no ip http server
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
!
line vty 0 15
 transport input ssh
"""


def _titles(result) -> list[str]:
    return [finding.title for finding in result.findings]


def test_it_reads_what_the_config_is_before_judging_it(db_session):
    result = configcheck.check(db_session, MESSY, filename="test-sw-01.txt")
    assert result.hostname == "test-sw-01"
    assert result.vendor_label.startswith("Cisco")
    assert result.vlans == 1
    assert result.routes == 1


def test_the_heavy_ones_come_first(db_session):
    """下まで読まないと大事な指摘に気づかない、を避ける。"""
    result = configcheck.check(db_session, MESSY)
    levels = [finding.level for finding in result.findings]
    assert levels[0] == HIGH
    assert levels == sorted(levels, key=lambda level: {HIGH: 0, MEDIUM: 1, LOW: 2}[level])


def test_telnet_and_default_snmp_are_called_out(db_session):
    result = configcheck.check(db_session, MESSY)
    titles = " / ".join(_titles(result))
    assert "telnet" in titles
    assert "SNMP" in titles


def test_a_readable_password_is_called_out(db_session):
    result = configcheck.check(db_session, MESSY)
    assert any("読める形" in title for title in _titles(result))


def test_a_next_hop_nobody_can_reach_is_found_here_too(db_session):
    result = configcheck.check(db_session, MESSY)
    assert any("届きません" in title for title in _titles(result))


def test_an_address_left_on_a_shut_port_is_mentioned(db_session):
    result = configcheck.check(db_session, MESSY)
    finding = next(f for f in result.findings if "止めてある" in f.title)
    assert finding.level == MEDIUM
    assert any("Vlan20" in line for line in finding.evidence)


def test_ports_without_any_description_are_mentioned(db_session):
    result = configcheck.check(db_session, MESSY)
    assert any("説明が1つも" in title for title in _titles(result))


def test_a_tidy_config_is_told_so_plainly(db_session):
    result = configcheck.check(db_session, TIDY)
    assert result.findings == []
    assert any("気になるところはありません" in note for note in result.notes)


def test_it_says_what_it_did_not_look_at(db_session):
    """見ていないところを黙っていると、全部見たと思われる。"""
    result = configcheck.check(db_session, TIDY)
    assert any("実機の状態や業務の要件は見ていません" in note for note in result.notes)


def test_an_unknown_vendor_is_said_rather_than_guessed(db_session):
    result = configcheck.check(db_session, "これは Config ではありません\n")
    assert any("判別できませんでした" in note for note in result.notes)


def test_nothing_is_written_to_the_ledger(db_session):
    """見るだけの口が書き込むと、確かめるつもりが増やすことになる。"""
    configcheck.check(db_session, MESSY)
    assert db_session.query(Device).count() == 0
    assert db_session.query(ConfigSnapshot).count() == 0


# ---------------------------------------------------------------------------
# 覚えた流儀との違い
# ---------------------------------------------------------------------------


@pytest.fixture()
def learned(db_session):
    body = """hostname {name}
!
vlan 10
 name OFFICE
!
vlan 20
 name WIFI
!
interface Vlan10
 description office
 ip address 10.10.{n}.1 255.255.255.0
!
no ip http server
logging 10.0.0.9
ntp server 10.0.0.10
!
line vty 0 15
 transport input ssh
"""
    for index, name in enumerate(("hq-sw-01", "hq-sw-02", "hq-sw-03")):
        device = Device(hostname=name, vendor="Cisco", model="C9300")
        db_session.add(device)
        db_session.flush()
        db_session.add(
            ConfigSnapshot(
                device_id=device.id,
                hostname=name,
                filename=f"{name}.txt",
                content=body.format(name=name, n=index + 1),
                sha256=str(index) * 64,
            )
        )
    db_session.commit()
    return db_session


def test_a_line_this_site_always_has_is_pointed_out_when_missing(learned):
    result = configcheck.check(learned, TIDY)
    finding = next(f for f in result.differences if "入っていない行" in f.title)
    assert finding.level == LOW
    assert any("logging 10.0.0.9" in line for line in finding.evidence)


def test_a_missing_log_destination_is_separate_from_a_mistake(learned):
    """間違いとは言い切れないので、指摘とは分けて出す。"""
    result = configcheck.check(learned, TIDY)
    assert any("ログの送り先が入っていません" in f.title for f in result.differences)
    assert not any("ログの送り先" in f.title for f in result.findings)


def test_a_different_destination_is_told_apart_from_a_missing_one(learned):
    body = TIDY + "logging 10.99.99.9\nntp server 10.0.0.10\n"
    result = configcheck.check(learned, body)
    finding = next(f for f in result.differences if "ログの送り先が、この現場" in f.title)
    assert any("10.99.99.9" in line for line in finding.evidence)


def test_without_enough_history_no_house_style_is_claimed(db_session):
    result = configcheck.check(db_session, TIDY)
    assert result.differences == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_the_api_takes_pasted_text(client):
    res = client.post(f"{API}/config-check", json={"text": MESSY, "filename": "a.txt"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hostname"] == "test-sw-01"
    assert body["findings"]
    assert all(item["why"] for item in body["findings"])


def test_the_api_refuses_an_empty_paste(client):
    assert client.post(f"{API}/config-check", json={"text": "   "}).status_code == 422


def test_the_api_takes_dropped_files(client):
    res = client.post(
        f"{API}/config-check/upload",
        files=[
            ("files", ("a.txt", MESSY.encode("utf-8"), "text/plain")),
            ("files", ("b.txt", TIDY.encode("cp932"), "text/plain")),
        ],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [item["filename"] for item in body] == ["a.txt", "b.txt"]
    assert body[1]["findings"] == []


def test_a_file_in_shift_jis_is_read(client):
    text = TIDY.replace("description office", "description 事務所")
    res = client.post(
        f"{API}/config-check/upload",
        files=[("files", ("sjis.txt", text.encode("cp932"), "text/plain"))],
    )
    assert res.status_code == 200
    assert res.json()[0]["hostname"] == "clean-sw-01"


def test_an_empty_file_is_refused(client):
    res = client.post(
        f"{API}/config-check/upload",
        files=[("files", ("empty.txt", io.BytesIO(b"   ").read(), "text/plain"))],
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 出した結果について聞き返す
# ---------------------------------------------------------------------------


def test_a_word_question_is_answered_without_a_model(db_session):
    """モデルを通すより速く、毎回同じ答えになり、人が直せる。"""
    from app.services import askback

    result = askback.ask(db_session, "HSRP")
    assert result.source == "glossary"
    assert "引き継ぐ" in result.answer
    assert "狂うと何が起きるか" in result.answer


def test_an_empty_question_asks_for_one(db_session):
    from app.services import askback

    assert "書いてください" in askback.ask(db_session, "  ").answer


def test_without_a_model_it_hands_back_something_to_paste(db_session):
    """端末にモデルを置けない職場では、これが唯一の道になる。"""
    from app.services import askback
    from app.services.l3review import HIGH, Finding

    result = askback.ask(
        db_session,
        "この構成でインターネットに出られますか",
        findings=[Finding(level=HIGH, title="届きません", why="隣に居ません", evidence=["A"])],
        context="hostname sw-01\n",
    )
    assert result.source == "prompt"
    assert result.prompt
    assert "届きません" in result.prompt
    assert "hostname sw-01" in result.prompt
    assert "社外に出してよい内容か" in result.note


def test_the_material_says_to_use_only_what_is_given(db_session):
    """材料に無いことを足されると、確かめようがない答えになる。"""
    from app.services import askback

    result = askback.ask(db_session, "この構成でインターネットに出られますか", context="x")
    assert "この材料に書いてあることだけを使って答えてください" in (result.prompt or "")


def test_the_api_answers_and_says_how(client):
    res = client.post(
        f"{API}/config-check/ask",
        json={"question": "トランクって何ですか", "context": ""},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "glossary"
    assert body["used"] == ["トランク"]


def test_the_api_takes_the_config_as_the_subject(client):
    res = client.post(
        f"{API}/config-check/ask",
        json={"question": "この機器は外に出られますか", "context": MESSY},
    )
    body = res.json()
    assert body["source"] in {"model", "prompt"}
    if body["source"] == "prompt":
        assert "届きません" in body["prompt"]
