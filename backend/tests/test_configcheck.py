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
logging host 10.10.10.9
ntp server 10.10.10.9
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
    """間違いとは言い切れないので、指摘とは分けて出す。

    単体でも「入っていません」は言えるが、現場の送り先まで書けるこちらの
    ほうが直しやすい。同じことを2度言わないよう、単体の側からは外す。
    """
    body = "\n".join(line for line in TIDY.splitlines() if not line.startswith(("logging", "ntp")))
    result = configcheck.check(learned, body)
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


# ---------------------------------------------------------------------------
# 単体で言える検算（機器に紐づかなくても分かること）
# ---------------------------------------------------------------------------

RISKY = """hostname risky-sw-01
!
no service password-encryption
ip source-route
ip ssh version 1
snmp-server community s3cret RW
username ops password ops123
!
vlan 10
 name OFFICE
vlan 30
 name UNUSED
!
interface GigabitEthernet1/0/1
 description uplink
 switchport mode trunk
 switchport trunk allowed vlan 10
 speed 1000
!
interface Vlan10
 description office
 ip address 10.10.10.1 255.255.255.0
!
logging host 10.0.0.9
ntp server 10.0.0.10
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
!
line vty 0 15
 transport input ssh
 exec-timeout 0 0
"""


def test_a_writable_snmp_is_heavier_than_a_readable_one(db_session):
    """合言葉を知られた時点で、設定を変えられる。"""
    result = configcheck.check(db_session, RISKY)
    finding = next(f for f in result.findings if "書き換えられます" in f.title)
    assert finding.level == HIGH


def test_the_old_ssh_and_the_plain_passwords_are_called_out(db_session):
    titles = " / ".join(_titles(configcheck.check(db_session, RISKY)))
    assert "SSH が古い方式" in titles
    assert "そのまま見える" in titles
    assert "元に戻せる形" in titles


def test_a_session_that_never_closes_is_called_out(db_session):
    result = configcheck.check(db_session, RISKY)
    assert any("いつまでも閉じません" in title for title in _titles(result))


def test_the_untagged_vlan_left_at_one_is_called_out(db_session):
    """分けたはずのものが1つにつながる、いちばん危ない食い違い。"""
    result = configcheck.check(db_session, RISKY)
    finding = next(f for f in result.findings if "タグ無し VLAN" in f.title)
    assert finding.level == MEDIUM
    assert any("GigabitEthernet1/0/1" in line for line in finding.evidence)


def test_a_vlan_nobody_uses_is_mentioned(db_session):
    result = configcheck.check(db_session, RISKY)
    finding = next(f for f in result.findings if "使っていない VLAN" in f.title)
    assert finding.level == LOW
    assert any("30" in line for line in finding.evidence)


def test_speed_fixed_on_one_side_only_is_mentioned(db_session):
    """通ってはいるので気づかれず、遅いという申告だけが残る。"""
    result = configcheck.check(db_session, RISKY)
    assert any("片方だけ固定" in title for title in _titles(result))


def test_a_config_that_sends_nowhere_is_told_it_leaves_no_trace(db_session):
    body = "\n".join(line for line in TIDY.splitlines() if not line.startswith(("logging", "ntp")))
    titles = _titles(configcheck.check(db_session, body))
    assert "ログの送り先が入っていません" in titles
    assert "時刻の参照先が入っていません" in titles


# ---------------------------------------------------------------------------
# ループを止める仕組み（STP）
# ---------------------------------------------------------------------------

STP_LOOSE = """
hostname sw-loose
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/1
 description 端末
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
!
interface GigabitEthernet1/0/24
 description 上位へ
 switchport mode trunk
 spanning-tree portfast
!
"""

STP_TIDY = """
hostname sw-tidy
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/1
 description 端末
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
 spanning-tree bpduguard enable
!
interface GigabitEthernet1/0/24
 description 上位へ
 switchport mode trunk
!
"""


def test_portfast_without_bpdu_guard_is_called_out(db_session):
    """端末用の口にスイッチを挿されると、輪ができて網ごと止まる。"""
    result = configcheck.check(db_session, STP_LOOSE)
    finding = next(f for f in result.findings if "BPDU guard" in f.title)
    assert finding.level == MEDIUM
    assert any("GigabitEthernet1/0/1" in line for line in finding.evidence)


def test_portfast_on_a_trunk_is_called_out(db_session):
    result = configcheck.check(db_session, STP_LOOSE)
    finding = next(f for f in result.findings if "portfast が入っています" in f.title)
    assert finding.level == MEDIUM
    assert any("GigabitEthernet1/0/24" in line for line in finding.evidence)


def test_a_missing_stp_mode_is_mentioned(db_session):
    text = STP_LOOSE.replace("spanning-tree mode rapid-pvst\n", "")
    titles = _titles(configcheck.check(db_session, text))
    assert any("STP のモードを決めていません" in title for title in titles)


def test_a_tidy_stp_config_says_nothing_about_stp(db_session):
    titles = _titles(configcheck.check(db_session, STP_TIDY))
    assert not [t for t in titles if "BPDU guard" in t or "portfast" in t or "STP の" in t]


# ---------------------------------------------------------------------------
# 口に誰を入れるか（802.1X / ポートセキュリティ）
# ---------------------------------------------------------------------------

ADMISSION = """
hostname sw-adm
dot1x system-auth-control
!
interface GigabitEthernet1/0/1
 switchport mode access
 authentication port-control auto
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport port-security
 switchport port-security maximum 2
!
interface GigabitEthernet1/0/3
 switchport mode access
 switchport port-security
 switchport port-security violation protect
!
"""


def test_dot1x_without_a_radius_is_the_heaviest(db_session):
    """本人確認を頼む相手が無い。その口には誰も入れない。"""
    result = configcheck.check(db_session, ADMISSION)
    finding = next(f for f in result.findings if "問い合わせ先がありません" in f.title)
    assert finding.level == HIGH
    assert any("GigabitEthernet1/0/1" in line for line in finding.evidence)


def test_dot1x_enabled_but_applied_nowhere_is_mentioned(db_session):
    """入れたつもりで、誰の確認もしていない状態。"""
    text = ADMISSION.replace(" authentication port-control auto\n", "")
    titles = _titles(configcheck.check(db_session, text))
    assert any("どの口にも当てていません" in title for title in titles)


def test_port_security_without_a_violation_action_is_mentioned(db_session):
    result = configcheck.check(db_session, ADMISSION)
    finding = next(f for f in result.findings if "破ったときの動き" in f.title)
    assert finding.level == LOW
    assert any("GigabitEthernet1/0/2" in line for line in finding.evidence)


def test_protect_leaves_no_trace_and_is_mentioned(db_session):
    result = configcheck.check(db_session, ADMISSION)
    finding = next(f for f in result.findings if "黙って落とす" in f.title)
    assert any("GigabitEthernet1/0/3" in line for line in finding.evidence)


def test_a_tidy_admission_config_says_nothing_about_it(db_session):
    tidy = """
hostname sw-ok
dot1x system-auth-control
radius-server host 10.0.0.53 key secret
!
interface GigabitEthernet1/0/1
 switchport mode access
 authentication port-control auto
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport port-security
 switchport port-security violation restrict
!
"""
    titles = _titles(configcheck.check(db_session, tidy))
    assert not [
        t
        for t in titles
        if "問い合わせ先" in t or "どの口にも" in t or "破ったとき" in t or "黙って落とす" in t
    ]


def test_a_vlan_used_on_a_port_but_never_defined_is_the_heaviest(db_session):
    """定義していない番号を当てたポートは、つながっているのに通らない。"""
    text = "hostname sw\nvlan 10\n name USERS\ninterface Gi1/0/1\n switchport access vlan 20\n"
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "定義が無い VLAN" in f.title)
    assert finding.level == HIGH
    assert any("VLAN 20" in line for line in finding.evidence)


def test_a_config_without_any_vlan_definition_is_not_accused(db_session):
    """ルーターの抜粋など、定義を1つも読めていないときは言わない。"""
    text = (
        "hostname rt\n"
        "interface Gi0/0.20\n"
        " encapsulation dot1Q 20\n"
        " ip address 10.0.20.1 255.255.255.0\n"
    )
    result = configcheck.check(db_session, text)
    assert not [f for f in result.findings if "定義が無い VLAN" in f.title]


def test_default_snmp_communities_are_caught_in_junos_and_comware_too(db_session):
    """public のままなのは、書き方が違っても同じ穴。"""
    junos = "set system host-name rt\nset snmp community public authorization read-write\n"
    result = configcheck.check(db_session, junos)
    titles = _titles(result)
    assert "SNMP の合言葉が既定のままです" in titles
    assert "SNMP から設定を書き換えられます" in titles

    comware = "sysname sw\nsnmp-agent community write private\n"
    titles = _titles(configcheck.check(db_session, comware))
    assert "SNMP の合言葉が既定のままです" in titles
    assert "SNMP から設定を書き換えられます" in titles


def test_telnet_left_open_is_caught_in_every_vendor_s_wording(db_session):
    samples = {
        "junos": "set system host-name rt\nset system services telnet\n",
        "fortios": 'config system global\n    set hostname "fw"\nend\n'
        "config system interface\n"
        '    edit "port1"\n'
        "        set allowaccess ping https ssh telnet\n"
        "    next\nend\n",
        "yamaha": "# RTX830 Rev.15.02.28\ntelnetd service on\n",
        "allied": "! Allied Telesis AlliedWare Plus (tm) version 5.5.2\n"
        "hostname sw\nservice telnet\n",
        "comware": "sysname sw\ntelnet server enable\n",
        "asa": "ASA Version 9.16(4)\nhostname fw\ntelnet 10.0.0.0 255.255.255.0 inside\n",
    }
    for vendor, text in samples.items():
        titles = _titles(configcheck.check(db_session, text))
        assert "telnet で入れる状態です" in titles, vendor
    # 止めた形は言わない
    titles = _titles(configcheck.check(db_session, "hostname sw\nno service telnet\n"))
    assert "telnet で入れる状態です" not in titles


def test_dot1x_without_radius_is_caught_in_aruba_junos_and_comware_too(db_session):
    samples = {
        "aruba": (
            "!Version ArubaOS-CX FL.10.09.0010\nhostname sw\n"
            "aaa authentication port-access dot1x authenticator\n    enable\n"
            "interface 1/1/5\n    no shutdown\n"
            "    aaa authentication port-access dot1x authenticator\n        enable\n"
        ),
        "junos": (
            "set system host-name sw\n"
            "set protocols dot1x authenticator interface ge-0/0/5.0 supplicant multiple\n"
        ),
        "comware": "sysname sw\ndot1x\ninterface GigabitEthernet1/0/5\n dot1x\n",
    }
    for vendor, text in samples.items():
        titles = _titles(configcheck.check(db_session, text))
        assert "802.1X を当てていますが、問い合わせ先がありません" in titles, vendor
    # 問い合わせ先があれば言わない
    with_server = samples["junos"] + "set access radius-server 10.0.0.9 secret x\n"
    titles = _titles(configcheck.check(db_session, with_server))
    assert "802.1X を当てていますが、問い合わせ先がありません" not in titles
