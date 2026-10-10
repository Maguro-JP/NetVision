"""渡された Config を、台帳に入れずにその場で見る。

「これ、どこか変じゃないか」を先に知りたい場面のほうが多い。試験用に
書いたもの、業者から届いたもの、自分で書いてみたもの。
"""

import io
from pathlib import Path

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import configcheck, configparse, defaults
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
ip ssh version 2
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
    assert any("行けません" in title for title in _titles(result))


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
        assert "行けません" in body["prompt"]


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


def test_a_per_port_bpdu_filter_is_called_out(db_session):
    """口ごとの BPDU フィルタは、その口の STP を止めるのと同じ。

    出典: Layer 2 Configuration Guide, Cisco IOS XE 17.18.x（Catalyst 9400）
    「Bridge Protocol Data Unit Filtering」: Enabling BPDU filtering on an
    interface is the same as disabling spanning tree on it and can result in
    spanning-tree loops.
    """
    text = """
hostname sw-filter
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/24
 switchport mode trunk
 spanning-tree bpdufilter enable
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "BPDU フィルタ" in f.title)
    assert finding.level == HIGH
    assert any("trunk の口" in line for line in finding.evidence)


def test_loop_guard_on_an_edge_port_is_mentioned(db_session):
    """loop guard は portfast の口には掛けられない。

    出典: Catalyst 6500「Configuring Optional STP Features」Loop Guard:
    You cannot enable loop guard on PortFast-enabled ports.
    """
    text = """
hostname sw-loop
spanning-tree mode rapid-pvst
!
interface GigabitEthernet1/0/1
 switchport mode access
 spanning-tree portfast
 spanning-tree bpduguard enable
 spanning-tree guard loop
!
"""
    titles = _titles(configcheck.check(db_session, text))
    assert any("loop guard" in title for title in titles)


def test_a_tidy_stp_config_says_nothing_about_the_new_checks(db_session):
    titles = _titles(configcheck.check(db_session, STP_TIDY))
    assert not [t for t in titles if "BPDU フィルタ" in t or "loop guard" in t]


def test_a_missing_stp_mode_is_mentioned(db_session):
    text = STP_LOOSE.replace("spanning-tree mode rapid-pvst\n", "")
    titles = _titles(configcheck.check(db_session, text))
    assert any("STP のモードを決めていません" in title for title in titles)


def test_a_tidy_stp_config_says_nothing_about_stp(db_session):
    titles = _titles(configcheck.check(db_session, STP_TIDY))
    assert not [t for t in titles if "BPDU guard" in t or "portfast" in t or "STP の" in t]


PORTCHANNEL = """
hostname sw-po
!
interface GigabitEthernet1/0/1
 switchport mode trunk
 switchport trunk allowed vlan 10,20
 channel-group 1 mode active
!
interface GigabitEthernet1/0/2
 switchport mode trunk
 switchport trunk allowed vlan 10
 channel-group 1 mode on
!
interface GigabitEthernet1/0/3
 switchport mode trunk
 switchport trunk allowed vlan 30
 channel-group 2 mode active
!
interface GigabitEthernet1/0/4
 switchport mode trunk
 switchport trunk allowed vlan 30
 channel-group 2 mode active
!
"""


def test_a_reserved_vlan_number_is_called_out(db_session):
    """1002〜1005 は古い媒体に取ってある帯。当てても通らない。

    出典: VLAN Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9500）
    「Configuring VLANs」: VLANs are identified by a number from 1 to 4094.
    VLAN IDs 1002 through 1005 are reserved for Token Ring and FDDI VLANs.
    """
    text = """
hostname sw-vlan
vlan 10
 name USERS
vlan 1003
 name OLD
!
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 1003
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 10
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "使えない番号" in f.title)
    assert finding.level == HIGH
    assert any("VLAN 1003" in line for line in finding.evidence)
    assert not any("VLAN 10:" in line for line in finding.evidence)


def test_a_native_vlan_left_out_of_the_allowed_list_is_called_out(db_session):
    """タグ無しの VLAN が許可の一覧に無いと、その口はその VLAN の一員になれない。

    出典: VLAN Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9300）
    「Configuring VLAN Trunks」: A trunk port can become a member of a VLAN if
    the VLAN is enabled, if VTP knows of the VLAN, and if the VLAN is in the
    allowed list for the port.
    """
    text = """
hostname sw-tr
vlan 10
vlan 20
vlan 99
!
interface GigabitEthernet1/0/24
 switchport mode trunk
 switchport trunk native vlan 99
 switchport trunk allowed vlan 10,20
!
interface GigabitEthernet1/0/23
 switchport mode trunk
 switchport trunk native vlan 99
 switchport trunk allowed vlan 10,20,99
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "許可の一覧から外して" in f.title)
    assert finding.level == HIGH
    assert any("GigabitEthernet1/0/24" in line for line in finding.evidence)
    assert not any("GigabitEthernet1/0/23" in line for line in finding.evidence)


def test_an_nxos_internal_vlan_is_called_out(db_session):
    """NX-OS は 3968〜4047 を装置の中で使う。

    出典: Cisco Nexus 9000 Series NX-OS Interfaces Configuration Guide
    「Configuring the Allowed VLANs for Trunking Ports」: VLANs 3968 to 4047
    are the default VLANs reserved for internal use by default.
    """
    text = """
hostname nx-01
version 9.3(10) Bios:version
feature interface-vlan
vlan 4000
  name INTERNAL
!
interface Ethernet1/1
  switchport
  switchport mode access
  switchport access vlan 4000
"""
    result = configcheck.check(db_session, text)
    assert any("使えない番号" in f.title for f in result.findings)


def test_dhcp_snooping_that_is_not_actually_working_is_called_out(db_session):
    """全体で有効にしただけ／信用する口が無い、はどちらも効いていない。

    出典: Catalyst 3560 Switch Software Configuration Guide, Cisco IOS Release
    15.0(2)SE「Configuring DHCP Features and IP Source Guard」: DHCP snooping is
    not active until DHCP snooping is enabled on a VLAN. / For DHCP snooping to
    function properly, all DHCP servers must be connected to the switch through
    trusted interfaces.
    """
    only_global = "hostname sw-sn1\nip dhcp snooping\n"
    titles = _titles(configcheck.check(db_session, only_global))
    assert any("どの VLAN にも掛けていません" in t for t in titles)

    no_trust = only_global.replace("sw-sn1", "sw-sn2") + "ip dhcp snooping vlan 10,20\n"
    titles = _titles(configcheck.check(db_session, no_trust))
    assert any("信用する口を1つも決めていません" in t for t in titles)

    tidy = (
        no_trust.replace("sw-sn2", "sw-sn3")
        + "interface GigabitEthernet1/0/24\n switchport mode trunk\n ip dhcp snooping trust\n"
    )
    titles = _titles(configcheck.check(db_session, tidy))
    assert not [t for t in titles if "DHCP の見張り" in t]


def test_arp_inspection_that_would_break_connectivity_is_called_out(db_session):
    """信用する口が無い／判断のもとが無い、はどちらも不通になる形。

    出典: Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9300）
    「Configuring Dynamic ARP Inspection」: By default, all interfaces are
    untrusted. / Configuring interfaces as untrusted when they should be
    trusted can result in a loss of connectivity. / Dynamic ARP inspection
    depends on the entries in the DHCP snooping binding database.
    """
    bare = "hostname sw-ai1\nip arp inspection vlan 10\n"
    titles = _titles(configcheck.check(db_session, bare))
    assert any("ARP の見張りで、信用する口" in t for t in titles)
    assert any("判断のもとがありません" in t for t in titles)

    tidy = (
        "hostname sw-ai2\nip dhcp snooping\nip dhcp snooping vlan 10\n"
        "ip arp inspection vlan 10\n"
        "interface GigabitEthernet1/0/24\n switchport mode trunk\n"
        " ip dhcp snooping trust\n ip arp inspection trust\n"
    )
    titles = _titles(configcheck.check(db_session, tidy))
    assert not [t for t in titles if "ARP の見張り" in t]


def test_weak_password_types_are_called_out_without_showing_the_secret(db_session):
    """type 5 / 7 は弱い。合言葉そのものは証跡に出さない。

    出典: Cisco Trust Center「Protecting Secrets on Cisco Network Devices」
    （type 0 / 4 / 5 / 7 は Secure ではない、8 / 9 / 10 は Secure）、
    NSA「Cisco Password Types: Best Practices」（Type 8 を推奨、0・4・7 は使うな）。
    """
    text = (
        "hostname sw-pw\n"
        "enable secret 5 $1$abcd$efghijklmnop\n"
        "username ops password 7 070C285F4D06\n"
        "username admin secret 9 $9$abcdefghijklmn\n"
        "interface GigabitEthernet1/0/1\n switchport mode access\n"
    )
    result = configcheck.check(db_session, text)
    weak = [f for f in result.findings if "合言葉が" in f.title]
    assert any("type 7" in f.title for f in weak)
    assert any("type 5" in f.title for f in weak)
    # 合言葉そのものは出さない
    joined = " ".join(line for f in weak for line in f.evidence)
    assert "$1$abcd" not in joined
    assert "070C285F4D06" not in joined
    # type 9 は言わない
    assert "admin" not in joined


def test_an_svi_without_the_feature_enabled_is_called_out(db_session):
    """NX-OS は feature interface-vlan を入れてからでないと SVI を作れない。

    出典: Cisco Nexus 9000 Series NX-OS Interfaces Configuration Guide,
    Release 10.6(x)「Configuring Layer 3 Interfaces」: Enable the VLAN network
    interface feature using the feature interface-vlan configuration.
    """
    text = """
hostname nx-01
feature lacp
vlan 10
interface Vlan10
  no shutdown
  ip address 10.0.0.1/24
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "VLAN の口を作る機能" in f.title)
    assert finding.level == HIGH
    assert any("Vlan10" in line for line in finding.evidence)

    tidy = text.replace("feature lacp\n", "feature lacp\nfeature interface-vlan\n")
    titles = _titles(configcheck.check(db_session, tidy))
    assert not [t for t in titles if "VLAN の口を作る機能" in t]


def test_the_same_vlan_for_voice_and_data_is_called_out(db_session):
    """公式が「同時に同じ番号にするな」と書いている。

    出典: Security Configuration Guide, Cisco IOS XE 17.16.x（Catalyst 9200）
    「Configuring IEEE 802.1x Port-Based Authentication」: Do not configure the
    same VLAN ID for both voice VLAN and access VLAN at the same time, because
    it may cause authentication failures.
    """
    text = """
hostname sw-voice2
vlan 10
vlan 20
!
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 10
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 20
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "電話用と端末用" in f.title)
    assert any("GigabitEthernet1/0/1" in line for line in finding.evidence)
    assert not any("GigabitEthernet1/0/2" in line for line in finding.evidence)


def test_a_mismatched_etherchannel_member_is_called_out(db_session):
    """そろっていない口は束に入らない。落ちないので気づきにくい。

    出典: Layer 2 Configuration Guide, Cisco IOS XE 26.x.x（Catalyst 9500）
    「Layer 2 EtherChannel Configuration Guidelines」: An EtherChannel supports
    the same allowed range of VLANs on all the ports in a trunking Layer 2
    EtherChannel. If the allowed range of VLANs is not the same, the ports do
    not form an EtherChannel.
    """
    result = configcheck.check(db_session, PORTCHANNEL)
    finding = next(f for f in result.findings if "束ね 1" in f.title)
    assert finding.level == HIGH
    assert any("許可 VLAN" in line for line in finding.evidence)
    assert any("束ね方" in line for line in finding.evidence)


def test_a_tidy_etherchannel_says_nothing(db_session):
    titles = _titles(configcheck.check(db_session, PORTCHANNEL))
    assert not [t for t in titles if "束ね 2" in t]


def test_an_hsrp_group_beyond_version_one_is_called_out(db_session):
    """既定の版1では 0〜255 まで。VLAN 番号に合わせると超える。

    出典: Network Services Configuration Guide, Cisco IOS XE 17.x
    「HSRP Version 2」（In HSRP version 1, group numbers are restricted to the
    range from 0 to 255）。
    """
    text = """
hostname dist-sw-31
!
interface Vlan300
 ip address 10.0.30.2 255.255.255.0
 standby 300 ip 10.0.30.1
 standby 300 priority 110
!
interface Vlan350
 ip address 10.0.35.2 255.255.255.0
 standby version 2
 standby 350 ip 10.0.35.1
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "版1で使える範囲" in f.title)
    assert finding.level == HIGH
    assert any("Vlan300" in line for line in finding.evidence)
    assert not any("Vlan350" in line for line in finding.evidence)


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


def test_dot1x_on_the_port_without_the_global_switch_is_the_heaviest(db_session):
    """Cisco は全体の dot1x system-auth-control が無いと、口の設定が効かない。

    出典: 802.1X Authentication Services Configuration Guide, Cisco IOS
    Release 15SY「Enabling 802.1X Authentication」手順 5（Globally enables
    802.1X port-based authentication）、および Catalyst 3750-X 設定ガイド
    「the dot1x system-auth-control global configuration command only
    globally enables or disables 802.1x authentication」。
    """
    text = ADMISSION.replace("dot1x system-auth-control\n", "")
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "全体で有効にしていません" in f.title)
    assert finding.level == HIGH
    assert any("GigabitEthernet1/0/1" in line for line in finding.evidence)


def test_the_global_switch_is_not_asked_for_when_the_port_is_written_another_way(db_session):
    """他社の書き方（Aruba CX の port-access）に、Cisco の行を求めない。"""
    text = """
hostname sw-cx
aaa authentication port-access dot1x authenticator
radius-server host 10.0.0.53 key secret
!
interface 1/1/1
 aaa authentication port-access dot1x authenticator
!
"""
    titles = _titles(configcheck.check(db_session, text))
    assert not [t for t in titles if "全体で有効にしていません" in t]


def test_port_security_without_a_violation_action_is_mentioned(db_session):
    result = configcheck.check(db_session, ADMISSION)
    finding = next(f for f in result.findings if "破ったときの動き" in f.title)
    assert finding.level == LOW
    assert any("GigabitEthernet1/0/2" in line for line in finding.evidence)


def test_port_security_on_a_dynamic_port_is_mentioned(db_session):
    """公式の制限。静的な access / trunk の口にしか掛けられない。

    出典: Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9300）
    「Port Security Configuration Guidelines」: Port security can only be
    configured on static access ports or trunk ports.
    """
    text = """
hostname sw-dtp
radius-server host 10.0.0.53 key secret
!
interface GigabitEthernet1/0/9
 switchport mode dynamic auto
 switchport port-security
 switchport port-security violation restrict
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "動的な口" in f.title)
    assert any("GigabitEthernet1/0/9" in line for line in finding.evidence)


def test_a_phone_port_that_can_only_learn_one_mac_is_mentioned(db_session):
    """電話と PC で MAC が2つ出る。既定の1のままだと口が落ちる。

    出典: 同ガイドライン: When you enable port security on an interface that is
    also configured with a voice VLAN, set the maximum allowed secure addresses
    on the port to two.
    """
    text = """
hostname sw-voice
radius-server host 10.0.0.53 key secret
!
interface GigabitEthernet1/0/10
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 20
 switchport port-security
 switchport port-security violation restrict
!
interface GigabitEthernet1/0/11
 switchport mode access
 switchport voice vlan 20
 switchport port-security
 switchport port-security maximum 3
 switchport port-security violation restrict
!
"""
    result = configcheck.check(db_session, text)
    finding = next(f for f in result.findings if "覚えられる数が足りません" in f.title)
    assert any("GigabitEthernet1/0/10" in line for line in finding.evidence)
    assert not any("GigabitEthernet1/0/11" in line for line in finding.evidence)


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


def test_default_snmp_communities_are_caught_in_junos_and_necqx_too(db_session):
    """public のままなのは、書き方が違っても同じ穴。"""
    junos = "set system host-name rt\nset snmp community public authorization read-write\n"
    result = configcheck.check(db_session, junos)
    titles = _titles(result)
    assert "SNMP の合言葉が既定のままです" in titles
    assert "SNMP から設定を書き換えられます" in titles

    necqx = "sysname sw\nsnmp-agent community write private\n"
    titles = _titles(configcheck.check(db_session, necqx))
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
        "necqx": "sysname sw\ntelnet server enable\n",
        "asa": "ASA Version 9.16(4)\nhostname fw\ntelnet 10.0.0.0 255.255.255.0 inside\n",
    }
    for vendor, text in samples.items():
        titles = _titles(configcheck.check(db_session, text))
        assert "telnet で入れる状態です" in titles, vendor
    # 止めた形は言わない
    titles = _titles(configcheck.check(db_session, "hostname sw\nno service telnet\n"))
    assert "telnet で入れる状態です" not in titles


def test_dot1x_without_radius_is_caught_in_aruba_junos_and_necqx_too(db_session):
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
        "necqx": "sysname sw\ndot1x\ninterface GigabitEthernet1/0/5\n dot1x\n",
    }
    for vendor, text in samples.items():
        titles = _titles(configcheck.check(db_session, text))
        assert "802.1X を当てていますが、問い合わせ先がありません" in titles, vendor
    # 問い合わせ先があれば言わない
    with_server = samples["junos"] + "set access radius-server 10.0.0.9 secret x\n"
    titles = _titles(configcheck.check(db_session, with_server))
    assert "802.1X を当てていますが、問い合わせ先がありません" not in titles


def test_a_plain_password_is_caught_in_necqx_too(db_session):
    text = "sysname sw\nlocal-user admin\n password simple himitsu\n"
    titles = _titles(configcheck.check(db_session, text))
    assert "利用者のパスワードが、元に戻せる形で入っています" in titles
    text = "sysname sw\nlocal-user admin\n password cipher $c$3$xxxx\n"
    titles = _titles(configcheck.check(db_session, text))
    assert "利用者のパスワードが、元に戻せる形で入っています" not in titles


def test_a_port_that_negotiates_its_own_mode_is_called_out(db_session):
    """相手任せでトランクになる口は、挿した相手から全部の VLAN が見える。"""
    text = "hostname sw1\ninterface GigabitEthernet0/2\n switchport mode dynamic desirable\n"
    result = configcheck.check(db_session, text)
    assert any("相手任せ" in title for title in _titles(result))


def test_a_port_with_a_decided_mode_says_nothing_about_negotiation(db_session):
    text = "hostname sw1\ninterface GigabitEthernet0/2\n switchport mode access\n"
    result = configcheck.check(db_session, text)
    assert not any("相手任せ" in title for title in _titles(result))


def test_a_management_line_without_authentication_is_called_out(db_session):
    text = "hostname sw1\nline vty 0 4\n transport input ssh\n no login\n"
    result = configcheck.check(db_session, text)
    assert any("認証を求めていません" in title for title in _titles(result))


_BUNDLE = """hostname sw1
interface GigabitEthernet1/0/47
 channel-group 1 mode active
!
interface GigabitEthernet1/0/48
 channel-group 1 mode active
!
show etherchannel summary
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi1/0/47(P)  Gi1/0/48(s)
"""


def test_a_bundled_port_that_never_joined_is_called_out(db_session):
    """落ちるわけではないので気づかれないが、帯域はその分だけ出ていない。"""
    result = configcheck.check(db_session, _BUNDLE)
    titles = _titles(result)
    assert any("束に入っていないもの" in title for title in titles)


def test_a_bundle_where_everyone_joined_says_nothing(db_session):
    result = configcheck.check(db_session, _BUNDLE.replace("Gi1/0/48(s)", "Gi1/0/48(P)"))
    assert not any("束に入っていない" in title for title in _titles(result))


def test_without_the_show_output_we_do_not_guess(db_session):
    text = "hostname sw1\ninterface GigabitEthernet1/0/47\n channel-group 1 mode active\n"
    result = configcheck.check(db_session, text)
    assert not any("束に入っていない" in title for title in _titles(result))


_STP = """hostname sw1
interface GigabitEthernet0/1
 switchport mode trunk
!
interface GigabitEthernet0/3
 switchport mode trunk
!
show spanning-tree

VLAN0010
  Spanning tree enabled protocol rstp

Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi0/1               Root FWD 4         128.1    P2p
Gi0/3               Altn BLK 19        128.3    P2p

VLAN0020
Interface           Role Sts Cost      Prio.Nbr Type
------------------- ---- --- --------- -------- --------------------------------
Gi0/3               Altn BLK 19        128.3    P2p
"""


def test_a_port_blocked_by_stp_is_shown(db_session):
    """「つながっているのに通らない」の原因はここが多い。設定では分からない。"""
    result = configcheck.check(db_session, _STP)
    hit = next((row for row in result.findings if "塞いでいる口" in row.title), None)
    assert hit is not None
    assert hit.evidence == ["GigabitEthernet0/3: VLAN 10, 20"]


def test_a_forwarding_port_is_not_reported_as_blocked(db_session):
    result = configcheck.check(db_session, _STP.replace("BLK", "FWD"))
    assert not any("塞いでいる口" in row.title for row in result.findings)


_CRC = """hostname sw1
interface GigabitEthernet0/1
 switchport mode access
!
interface GigabitEthernet0/2
 switchport mode access
!
show interfaces
GigabitEthernet0/1 is up, line protocol is up (connected)
  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec
     31337 input errors, 31337 CRC, 0 frame, 0 overrun, 0 ignored
     0 output errors, 0 collisions, 2 interface resets
GigabitEthernet0/2 is up, line protocol is up (connected)
  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
     0 output errors, 0 collisions, 0 interface resets
"""


def test_a_port_receiving_broken_frames_is_called_out(db_session):
    """線・光・二重の食い違いでしか増えない。Config からは分からない。"""
    result = configcheck.check(db_session, _CRC)
    hit = next((row for row in result.findings if "壊れていた通信" in row.title), None)
    assert hit is not None
    assert hit.evidence == ["GigabitEthernet0/1: 31337 件"]


def test_clean_counters_say_nothing(db_session):
    text = _CRC.replace("31337 input errors, 31337 CRC", "0 input errors, 0 CRC")
    result = configcheck.check(db_session, text)
    assert not any("壊れていた通信" in row.title for row in result.findings)


def _hygiene_titles(text: str) -> list[str]:
    parsed = configparse.parse(text)
    return [item.title for item in configcheck._hygiene(parsed, text)]


def test_telnet_and_default_communities_are_caught_on_every_vendor() -> None:
    """telnet が開いている・合言葉が既定、はどのベンダーでも同じ事故。

    書き方が違うだけで見逃すと、そのベンダーの機器だけ黙って通る。
    """
    alaxala = "\n".join(
        [
            "Model AX3650S-24T4XW",
            'hostname "ax01"',
            'snmp-server community "public" ro',
            "line vty 0 4",
            " transport input telnet",
        ]
    )
    huawei = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "snmp-agent community read public",
            "user-interface vty 0 4",
            " protocol inbound telnet",
        ]
    )
    fastiron = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "snmp-server community public ro",
            "telnet server",
        ]
    )
    for text in (alaxala, huawei, fastiron):
        titles = _hygiene_titles(text)
        assert "telnet で入れる状態です" in titles, text.splitlines()[0]
        assert "SNMP の合言葉が既定のままです" in titles, text.splitlines()[0]


def test_a_quoted_community_with_write_access_is_caught() -> None:
    text = 'Model AX3650S-24T4XW\nhostname "ax01"\nsnmp-server community "netops" rw\n'
    assert "SNMP から設定を書き換えられます" in _hygiene_titles(text)


def test_a_tidy_config_says_nothing_here() -> None:
    text = "\n".join(
        [
            "version 17.9",
            "hostname c1",
            "snmp-server community S3cret RO",
            "line vty 0 4",
            " transport input ssh",
        ]
    )
    assert _hygiene_titles(text) == []


def test_a_baseline_is_only_compared_within_the_same_kind_of_device(client, db_session):
    """決まり文句は、同じ種類の機器どうしでしか比べられない。

    スイッチの `spanning-tree mode` を無線コントローラに当てると、入って
    いないのが当たり前の行を「抜けている」と言うことになる。
    """
    import hashlib

    from app.models import Device, DeviceType
    from app.models.topology import ConfigSnapshot

    def snapshot(device_id: int, name: str, body: str) -> ConfigSnapshot:
        return ConfigSnapshot(
            device_id=device_id,
            filename=f"{name}.txt",
            content=body,
            sha256=hashlib.sha256(body.encode()).hexdigest(),
        )

    switch_body = "\n".join(
        [
            "hostname {name}",
            "spanning-tree mode rapid-pvst",
            "logging host 10.0.99.11",
            "interface GigabitEthernet1/0/1",
            " switchport mode access",
        ]
    )
    for name in ("sw-01", "sw-02"):
        device = Device(hostname=name, vendor="Cisco", device_type=DeviceType.SWITCH)
        db_session.add(device)
        db_session.flush()
        db_session.add(snapshot(device.id, name, switch_body.format(name=name)))

    controller = Device(hostname="wlc-01", vendor="Cisco", device_type=DeviceType.OTHER)
    db_session.add(controller)
    db_session.flush()
    wlc_body = "hostname wlc-01\nlogging host 10.0.99.11\nwlan Corp 1 Corp-WiFi\n"
    db_session.add(snapshot(controller.id, "wlc-01", wlc_body))
    db_session.commit()

    titles = [item.title for item in configcheck.check(db_session, wlc_body).differences]
    assert not [title for title in titles if "入っていない行" in title]

    # スイッチどうしでは、これまでどおり比べる
    thin = "hostname sw-03\nlogging host 10.0.99.11\ninterface GigabitEthernet1/0/1\n"
    switch_titles = [item.title for item in configcheck.check(db_session, thin).differences]
    assert [title for title in switch_titles if "入っていない行" in title]


def test_an_edge_port_without_a_guard_is_caught_on_every_vendor() -> None:
    """端末をつなぐ口の言い方は機種で違う。

    1つの書き方だけを見ていると、他社の機器では黙って通る。誰かがスイッチを
    挿したときに網全体が止まる、といういちばん重い事故の手前の設定。
    """
    edge_only = {
        "cisco": "interface GigabitEthernet1/0/2\n spanning-tree portfast\n",
        "necqx": "interface GigabitEthernet1/0/2\n stp edged-port\n",
        "vrp": "interface 10GE1/0/2\n stp edged-port enable\n",
        "fastiron": "interface ethernet 1/1/2\n spanning-tree 802-1w admin-edge-port\n",
        "aruba": "interface 1/1/2\n spanning-tree port-type admin-edge\n",
    }
    for name, body in edge_only.items():
        titles = [item.title for item in configcheck._stp(body)]
        assert [title for title in titles if "BPDU guard" in title], name


def test_a_guard_written_in_any_of_its_spellings_is_accepted() -> None:
    guarded = {
        # 口ごとに書く形。ハイフンの有無は機種で違う
        "cisco": "interface GigabitEthernet1/0/2\n spanning-tree portfast\n"
        " spanning-tree bpduguard enable\n",
        "allied": "interface port1.0.2\n spanning-tree portfast\n"
        " spanning-tree portfast bpdu-guard enable\n",
        # 1行で全ポートに掛ける形
        "cisco-global": "spanning-tree portfast bpduguard default\n"
        "interface GigabitEthernet1/0/2\n spanning-tree portfast\n",
        "necqx-global": "stp bpdu-protection\ninterface GigabitEthernet1/0/2\n stp edged-port\n",
    }
    for name, body in guarded.items():
        titles = [item.title for item in configcheck._stp(body)]
        assert not [title for title in titles if "BPDU guard" in title], name


def test_vrp_dot1x_without_a_radius_server_is_caught() -> None:
    """VRP は口の下に `dot1x enable` と書く。

    その書き方を見ていないと、本人確認を頼む相手が居ないまま「入っている」
    ことになる。全社で使えなくなるほうへ倒れる設定なので、黙るのは困る。
    """
    text = "\n".join(
        [
            "VRP (R) software, Version 8.180 (S5720 V200R011C10SPC600)",
            "sysname hw1",
            "#",
            "interface GigabitEthernet0/0/1",
            " dot1x enable",
        ]
    )
    titles = [item.title for item in configcheck._port_admission(text)]
    assert [title for title in titles if "問い合わせ先がありません" in title]

    with_server = text.replace("#\n", "#\nradius-server template rad\n#\n", 1)
    quiet = [item.title for item in configcheck._port_admission(with_server)]
    assert not [title for title in quiet if "問い合わせ先がありません" in title]


def test_a_show_output_alone_is_not_told_its_settings_are_missing() -> None:
    """設定を渡されていないのに「入っていません」と言わないこと。

    show の出力だけを貼ることは普通にある。そこにログの送り先が無いのは
    当たり前で、無いのと、そもそも渡されていないのは別のことになる。
    """
    show_only = "\n".join(
        [
            "Cisco IOS XE Software, Version 17.09.04a",
            "cisco C9300-48P (X86) processor",
        ]
    )
    excerpt = "interface GigabitEthernet1/0/1\n switchport mode access\n"
    for text in (show_only, excerpt):
        parsed = configparse.parse(text)
        titles = [item.title for item in configcheck._findings(parsed, text)]
        assert not [title for title in titles if "入っていません" in title], text.splitlines()[0]


def test_a_whole_config_without_a_log_target_is_still_called_out() -> None:
    text = "version 17.9\nhostname c1\ninterface GigabitEthernet1/0/1\n description x\n"
    parsed = configparse.parse(text)
    titles = [item.title for item in configcheck._findings(parsed, text)]
    assert "ログの送り先が入っていません" in titles
    assert "時刻の参照先が入っていません" in titles


def test_one_pasted_port_is_not_called_a_device_without_descriptions() -> None:
    """1つの口だけを貼った抜粋を、機器の話にしない。

    「ポートに説明が1つも書かれていません」は機器についての指摘に読める。
    抜粋で言うと、書いてあるのに指摘されたように見える。
    """
    excerpt = "interface GigabitEthernet1/0/1\n switchport mode access\n"
    parsed = configparse.parse(excerpt)
    titles = [item.title for item in configcheck._findings(parsed, excerpt)]
    assert not [title for title in titles if "説明が1つも" in title]

    whole = "\n".join(
        [
            "version 17.9",
            "hostname c1",
            "interface GigabitEthernet1/0/1",
            " switchport mode access",
        ]
    )
    parsed = configparse.parse(whole)
    titles = [item.title for item in configcheck._findings(parsed, whole)]
    assert [title for title in titles if "説明が1つも" in title]


def test_a_fragment_is_not_told_its_acl_or_vlan_is_unused() -> None:
    """口が1つも見えていないなら、当てているかどうかは分からない。

    ACL だけ・VLAN の定義だけを貼った抜粋で「どこにも当たっていません」と
    言うと、当ててあるのに出る。直しようのない指摘になる。
    """
    acl_only = "ip access-list extended MGMT\n 10 permit ip 10.0.99.0 0.0.0.255 any\n"
    vlan_only = "vlan 10\n name USERS\n"
    for text in (acl_only, vlan_only):
        parsed = configparse.parse(text)
        titles = [item.title for item in configcheck._findings(parsed, text)]
        assert not [title for title in titles if "どこにも" in title], text.splitlines()[0]

    # 口まで見えていれば、これまでどおり指摘する
    whole = "\n".join(
        [
            "hostname c1",
            "ip access-list extended MGMT",
            " 10 permit ip 10.0.99.0 0.0.0.255 any",
            "interface GigabitEthernet1/0/1",
            " description x",
        ]
    )
    parsed = configparse.parse(whole)
    titles = [item.title for item in configcheck._findings(parsed, whole)]
    assert [title for title in titles if "どこにも当たっていません" in title]


def test_a_nat_or_policy_fragment_is_not_judged_by_what_it_cannot_show() -> None:
    """当て先が見えていない抜粋に、当て先の話をしない。

    NAT の行だけ・route-map だけを貼ることは普通にある。そこに内側の口や
    当てている場所が無いのは当たり前で、書いてあるのに出ると直せない。
    """
    nat_only = "ip nat inside source list 1 interface GigabitEthernet0/0 overload\n"
    policy_only = "route-map TO-ISP permit 10\n match ip address prefix-list P\n"
    for text in (nat_only, policy_only):
        parsed = configparse.parse(text)
        titles = [item.title for item in configcheck._findings(parsed, text)]
        assert not titles, (text.splitlines()[0], titles)

    whole = "\n".join(
        [
            "hostname c1",
            "interface GigabitEthernet0/0",
            " ip nat outside",
            "ip nat inside source list 1 interface GigabitEthernet0/0 overload",
            "access-list 1 permit 10.0.0.0 0.0.0.255",
        ]
    )
    parsed = configparse.parse(whole)
    titles = [item.title for item in configcheck._findings(parsed, whole)]
    assert [title for title in titles if "内側のポートが決まっていません" in title]


def test_option_82_from_a_layer2_switch_is_called_out(db_session):
    """見張りを入れた L2 のスイッチが、上位で落とされる形。

    option 82 の挿入は既定で有効。自分でリレー（ip helper-address）を
    していない L2 のスイッチがこれを付けると、中の giaddr は 0.0.0.0 の
    ままになり、上位がその口を信用していなければ落とす。

    出典:
    - Catalyst 6500 Supervisor Engine 2T Software Configuration Guide
      「Default Configuration for DHCP Snooping」（DHCP snooping information
      option: Enabled）
    - Catalyst 4500 Software Configuration Guide「Configuring DHCP Snooping」
      （the DHCP packets would be dropped on arriving on a snooping untrusted port）
    """
    base = (
        "hostname sw-o82\nip dhcp snooping\nip dhcp snooping vlan 10\n"
        "interface GigabitEthernet1/0/24\n switchport mode trunk\n ip dhcp snooping trust\n"
    )
    titles = _titles(configcheck.check(db_session, base))
    assert any("上位で落とされる形" in t for t in titles)

    # 自分で切ってあれば言わない
    off = base.replace("sw-o82", "sw-o82b") + "no ip dhcp snooping information option\n"
    assert not [t for t in _titles(configcheck.check(db_session, off)) if "上位で落とされる" in t]

    # 自分でリレーしているなら giaddr が入るので言わない
    relay = (
        base.replace("sw-o82", "sw-o82c")
        + "interface Vlan10\n ip address 10.0.10.1 255.255.255.0\n ip helper-address 10.9.9.9\n"
    )
    assert not [t for t in _titles(configcheck.check(db_session, relay)) if "上位で落とされる" in t]


def test_junos_edge_ports_without_bpdu_protection_are_called_out(db_session):
    """Junos で端末口にしてあるのに、BPDU から守っていない口を言う。

    出典: Junos OS「BPDU Protection for Spanning-Tree Protocols」
    （"you must protect edge ports by enabling bpdu-block-on-edge"）、
    CLI Reference の「edge」「bpdu-block-on-edge」。
    """
    text = "\n".join(
        [
            "set system host-name ex-01",
            "set protocols rstp interface ge-0/0/1 edge",
            "set protocols rstp interface ge-0/0/2 edge",
            "set protocols layer2-control bpdu-block interface ge-0/0/2",
        ]
    )
    result = configcheck.check(db_session, text, filename="ex-01.conf")
    titles = [f.title for f in result.findings]
    assert any("BPDU から守る設定が入っていません" in title for title in titles)
    evidence = [e for f in result.findings for e in f.evidence]
    assert any("ge-0/0/1" in item for item in evidence)
    # 口ごとに守ってある口は言わない
    assert not any("ge-0/0/2" in item for item in evidence)


def test_junos_bpdu_block_on_edge_covers_every_edge_port(db_session):
    """まとめて守る書き方（bpdu-block-on-edge）が入っていれば、言わない。"""
    text = "\n".join(
        [
            "set system host-name ex-02",
            "set protocols rstp bpdu-block-on-edge",
            "set protocols rstp interface ge-0/0/1 edge",
        ]
    )
    result = configcheck.check(db_session, text, filename="ex-02.conf")
    assert not any("BPDU から守る設定" in f.title for f in result.findings)


def test_a_junos_plain_text_password_in_the_config_text_is_called_out(db_session):
    """Junos の設定文に合言葉が字のまま書かれていたら言う。値は証跡に出さない。

    機器に入れた時点で暗号化されるので、この形が残っているのは投入前の設定文。

    出典: Junos OS CLI Reference「user (Access)」
    （"The plain text password will be automatically encrypted as soon as you
    configure it."）。
    """
    text = "\n".join(
        [
            "set system host-name ex-03",
            'set system root-authentication plain-text-password "sample-value"',
            'set system login user admin authentication encrypted-password "$6$sample"',
        ]
    )
    result = configcheck.check(db_session, text, filename="ex-03.conf")
    hit = next(f for f in result.findings if "字のまま" in f.title)
    assert hit.evidence == ["set system root-authentication plain-text-password"]
    # 合言葉そのものは、どこにも出さない
    assert all("sample-value" not in item for item in hit.evidence)
    assert "sample-value" not in hit.why


def test_a_port_level_bpdu_guard_is_not_read_as_a_device_wide_one(db_session):
    """口の中に書いた bpdu-guard を、機器全体の設定と取り違えない。

    Aruba CX は口の中に `spanning-tree bpdu-guard` と書く。これを全体の
    設定として読むと、1口守っただけで残りの口を見逃す。

    出典: AOS-CX の書き方（`spanning-tree port-type admin-edge` /
    `spanning-tree bpdu-guard` を口の下に書く形）。
    """
    text = "\n".join(
        [
            "!Version ArubaOS-CX GL.10.09.0010",
            "hostname cx-01",
            "spanning-tree mode rpvst",
            "interface 1/1/1",
            "    no shutdown",
            "    spanning-tree port-type admin-edge",
            "interface 1/1/2",
            "    no shutdown",
            "    spanning-tree port-type admin-edge",
            "    spanning-tree bpdu-guard",
        ]
    )
    result = configcheck.check(db_session, text, filename="cx-01.conf")
    hit = next(f for f in result.findings if "BPDU guard が入っていません" in f.title)
    assert any("1/1/1" in item for item in hit.evidence)
    assert not any("1/1/2" in item for item in hit.evidence)


def test_portfast_edge_is_read_as_an_edge_port(db_session):
    """IOS-XE は端末口を `spanning-tree portfast edge` とも書く。

    出典: Command Reference, Cisco IOS XE 17.16.x（Catalyst 9300 Switches）
    「spanning-tree portfast edge [trunk]」「spanning-tree portfast edge bpduguard default」。

    この書き方を読まないと、BPDU から守っていない端末口を見逃す。
    全体で `portfast edge bpduguard default` を入れてあれば守られている。
    """
    bare = (
        "hostname sw-pe1\n"
        "interface GigabitEthernet1/0/1\n switchport mode access\n spanning-tree portfast edge\n"
    )
    hit = [t for t in _titles(configcheck.check(db_session, bare)) if "BPDU guard" in t]
    assert hit

    guarded = bare.replace("sw-pe1", "sw-pe2") + "spanning-tree portfast edge bpduguard default\n"
    assert not [t for t in _titles(configcheck.check(db_session, guarded)) if "BPDU guard" in t]


def test_portfast_default_makes_access_ports_edge_ports(db_session):
    """全体の `spanning-tree portfast default` で、アクセスの口は端末口になる。

    出典: Layer 2 Configuration Guide, Cisco IOS XE 17.16.x（Catalyst 9300）
    「Enabling PortFast」（use the spanning-tree portfast default global
    configuration command to globally enable the PortFast feature on all
    nontrunking ports）。

    口ごとに portfast と書いていないので、口だけを見ていると BPDU から
    守っていない端末口を見逃す。
    """
    bare = (
        "hostname sw-pd1\nspanning-tree portfast default\n"
        "interface GigabitEthernet1/0/5\n switchport mode access\n"
        "interface GigabitEthernet1/0/48\n switchport mode trunk\n"
    )
    result = configcheck.check(db_session, bare)
    hit = next(f for f in result.findings if "BPDU guard が入っていません" in f.title)
    assert any("1/0/5" in item for item in hit.evidence)
    # トランクは端末口にならない
    assert not any("1/0/48" in item for item in hit.evidence)

    guarded = bare.replace("sw-pd1", "sw-pd2") + "spanning-tree portfast bpduguard default\n"
    assert not [t for t in _titles(configcheck.check(db_session, guarded)) if "BPDU guard" in t]


_SHOW_VTP = """hostname sw-vtp
Switch#show vtp status
VTP Version capable             : 1 to 3
VTP version running             : {version}
VTP Domain Name                 : Lab_Network
VTP Pruning Mode                : Disabled
VTP Traps Generation            : Disabled
Device ID                       : f87a.41a8.ca00

Feature VLAN:
--------------
VTP Operating Mode                : {mode}
Maximum VLANs supported locally   : 1005
Number of existing VLANs          : 6
Configuration Revision            : {revision}
"""


def test_vtp_v1_v2_server_with_a_revision_is_called_out(db_session):
    """出典: VLAN Configuration Guide, Cisco IOS XE 17.15.x（Catalyst 9300）
    「Configuring VTP」の Caution と、Command Reference 16.5.x の
    `show vtp status` の出力例。

    版 1・2 でサーバー／クライアントのままリビジョンが 0 でないと、
    つないだ先のドメインの VLAN を消しうる。版 3・transparent・
    リビジョン 0 は言わない。
    """
    risky = _SHOW_VTP.format(version=2, mode="Server", revision=4)
    assert any("VTP の版 1・2" in t for t in _titles(configcheck.check(db_session, risky)))

    for version, mode, revision in ((3, "Server", 4), (2, "Transparent", 4), (2, "Server", 0)):
        calm = _SHOW_VTP.format(version=version, mode=mode, revision=revision).replace(
            "sw-vtp", f"sw-vtp-{version}{mode}{revision}"
        )
        assert not [t for t in _titles(configcheck.check(db_session, calm)) if "VTP の版" in t]


def test_access_ports_left_on_vlan1_are_called_out(db_session):
    """出典: Cisco IOS XE 17 Interface Characteristics Configuration Guide
    「Default Ethernet interface configuration」（Default VLAN (for access
    ports): VLAN 1）。

    VLAN を書き忘れたアクセスの口は VLAN 1 に入る。止めてある口と、
    VLAN を書いてある口は言わない。
    """
    text = (
        "hostname sw-v1\n"
        "interface GigabitEthernet1/0/1\n switchport mode access\n"
        "interface GigabitEthernet1/0/2\n switchport mode access\n switchport access vlan 10\n"
        "interface GigabitEthernet1/0/3\n switchport mode access\n shutdown\n"
    )
    result = configcheck.check(db_session, text)
    hit = next(f for f in result.findings if "既定の VLAN 1" in f.title)
    assert any("1/0/1" in item for item in hit.evidence)
    assert not any("1/0/2" in item or "1/0/3" in item for item in hit.evidence)


def test_ports_with_a_vlan_but_no_mode_are_called_out(db_session):
    """出典: Cisco IOS XE 17 Interface Characteristics Configuration Guide
    「Default Ethernet interface configuration」（VLAN trunking: Switchport mode
    dynamic auto (supports DTP)）。

    VLAN だけ書いて mode を書かない口は dynamic auto のままで、相手次第で
    トランクになる。mode を書いた口と NX-OS の資料は言わない。
    """
    text = (
        "hostname sw-dtp\n"
        "interface GigabitEthernet1/0/1\n switchport access vlan 10\n"
        "interface GigabitEthernet1/0/2\n switchport mode access\n switchport access vlan 10\n"
        "interface GigabitEthernet1/0/3\n switchport access vlan 10\n shutdown\n"
    )
    result = configcheck.check(db_session, text)
    hit = next(f for f in result.findings if "相手次第でトランク" in f.title)
    assert any("1/0/1" in item for item in hit.evidence)
    assert not any("1/0/2" in item or "1/0/3" in item for item in hit.evidence)

    nxos = "hostname nx-dtp\nfeature lacp\ninterface Ethernet1/1\n  switchport access vlan 10\n"
    assert not [
        t for t in _titles(configcheck.check(db_session, nxos)) if "相手次第でトランク" in t
    ]


def test_ssh_that_still_accepts_version_1_is_called_out(db_session):
    """出典: Security and VPN Configuration Guide, Cisco IOS XE 17.x
    「Secure Shell Version 2 Support」（If you do not configure this command, SSH
    by default runs in compatibility mode）。

    SSH を使っていて ip ssh version 2 が無ければ、版 1 も受ける。
    """
    base = "hostname sw-ssh\nline vty 0 4\n transport input ssh\n"
    assert any("版 1 でもつながる" in t for t in _titles(configcheck.check(db_session, base)))

    fixed = base.replace("sw-ssh", "sw-ssh2") + "ip ssh version 2\n"
    assert not [t for t in _titles(configcheck.check(db_session, fixed)) if "版 1 でも" in t]


def test_fastiron_is_not_told_to_write_a_spanning_tree_mode(db_session):
    """FastIron には `spanning-tree mode` の書き方が無い。STP の種類は `spanning-tree`
    （VLAN ごとの 802.1D）・`spanning-tree single`・`spanning-tree 802-1w` で選ぶ。

    出典: Ruckus「FastIron 08.0.60 Layer 2 Switching Configuration Guide」の「Enabling or
    disabling the Spanning Tree Protocol (STP)」（Spanning-tree single / Per VLAN spanning tree /
    Spanning-tree single 802.1w）。
    """
    text = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "vlan 10 by port",
            " tagged ethe 1/1/1",
            " spanning-tree 802-1w",
            "!",
            "interface ethernet 1/1/2",
            " spanning-tree 802-1w admin-edge-port",
            "!",
        ]
    )
    result = configcheck.check(db_session, text, filename="icx1.txt")
    assert result.vendor == "fastiron"
    assert not [t for t in _titles(result) if "STP のモード" in t]


# 既定値に拠った指摘には、拠った資料の名前を添える（docs/knowledge/finding-sources.md）。
# 既定値は Config に書いていないので、根拠の行だけでは読み手が確かめに行けない。


def _sources_of(db_session, text: str, word: str) -> list[str]:
    result = configcheck.check(db_session, text)
    return next(f for f in result.findings if word in f.title).sources


@pytest.mark.parametrize(
    ("text", "word", "source"),
    [
        (
            "hostname sw-src1\ninterface GigabitEthernet1/0/1\n switchport mode access\n",
            "既定の VLAN 1",
            defaults.ACCESS_VLAN_SOURCE,
        ),
        (
            "hostname sw-src2\nip dhcp snooping\nip dhcp snooping vlan 10\n",
            "DHCP の見張りで、信用する口",
            defaults.DHCP_SNOOPING_UNTRUSTED_SOURCE,
        ),
        (
            "hostname sw-src3\nip dhcp snooping\nip dhcp snooping vlan 10\n"
            "interface GigabitEthernet1/0/24\n switchport mode trunk\n ip dhcp snooping trust\n",
            "上位で落とされる形",
            defaults.DHCP_OPTION82_SOURCE,
        ),
        (
            "hostname sw-src4\nip arp inspection vlan 10\n",
            "ARP の見張りで、信用する口",
            defaults.ARP_INSPECTION_UNTRUSTED_SOURCE,
        ),
        (
            "hostname sw-src5\ninterface Vlan300\n ip address 10.0.30.2 255.255.255.0\n"
            " standby 300 ip 10.0.30.1\n",
            "版1で使える範囲",
            defaults.HSRP_VERSION_SOURCE,
        ),
        (
            "hostname sw-src6\ninterface GigabitEthernet1/0/2\n switchport mode access\n"
            " switchport access vlan 10\n switchport port-security\n",
            "破ったときの動き",
            defaults.PORT_SECURITY_VIOLATION_SOURCE,
        ),
    ],
    ids=["access-vlan", "dhcp-snooping-trust", "option82", "arp-trust", "hsrp-v1", "violation"],
)
def test_a_finding_that_leans_on_a_default_names_its_source(db_session, text, word, source):
    assert _sources_of(db_session, text, word) == [source]


def test_a_finding_that_does_not_lean_on_a_default_has_no_source(db_session):
    """telnet を開けている、は書いてある行そのものの指摘。資料の名前は要らない。"""
    result = configcheck.check(db_session, MESSY)
    assert any(f.sources == [] for f in result.findings)
    assert all(not f.sources for f in result.findings if "telnet" in f.title.lower())


def test_the_config_check_api_carries_the_sources(client):
    text = "hostname sw-src7\ninterface GigabitEthernet1/0/1\n switchport mode access\n"
    body = client.post(f"{API}/config-check", json={"text": text}).json()
    finding = next(f for f in body["findings"] if "既定の VLAN 1" in f["title"])
    assert finding["sources"] == [defaults.ACCESS_VLAN_SOURCE]


VOICE_PORT = """
hostname sw-voice-src
radius-server host 10.0.0.53 key secret
!
interface GigabitEthernet1/0/10
 switchport mode access
 switchport access vlan 10
 switchport voice vlan 20
 switchport port-security
 switchport port-security violation restrict
!
"""


def test_a_voice_port_left_at_the_default_maximum_names_its_source(db_session):
    """上限を書いていない口は、既定の1で数えている。

    出典: Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9500）「Port Security」の
    Default Port Security Configuration
    （Maximum number of secure MAC addresses per port: One address）。
    """
    assert _sources_of(db_session, VOICE_PORT, "覚えられる数が足りません") == [
        defaults.PORT_SECURITY_MAXIMUM_SOURCE
    ]


def test_a_voice_port_with_a_written_maximum_of_one_needs_no_source(db_session):
    text = VOICE_PORT.replace(
        " switchport port-security violation restrict\n",
        " switchport port-security maximum 1\n switchport port-security violation restrict\n",
    )
    assert _sources_of(db_session, text, "覚えられる数が足りません") == []


def test_a_missing_stp_mode_on_ios_names_the_catalyst_default(db_session):
    """出典: Layer 2 Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9300）
    「Configuring Spanning Tree Protocol」の Default Spanning-Tree Configuration
    （Spanning-tree mode: Rapid PVST+ (PVST+ and MSTP are disabled.)）。
    """
    text = STP_LOOSE.replace("spanning-tree mode rapid-pvst\n", "")
    assert configparse.parse(text).vendor is configparse.Vendor.CISCO_IOS
    assert _sources_of(db_session, text, "STP のモードを決めていません") == [
        defaults.STP_MODE_SOURCE
    ]


def test_a_missing_stp_mode_on_eos_names_the_arista_default(db_session):
    """出典: Arista EOS User Manual「Spanning Tree Protocol」
    （By default, Arista switches use MSTP. / MSTP is the default STP version.）。
    Catalyst の資料（既定は Rapid PVST+）を出すと、既定のモードを取り違えさせる。
    """
    text = "! device: sw-a1 (DCS-7050SX3-48YC8, EOS-4.29.2F)\n" + STP_LOOSE.replace(
        "spanning-tree mode rapid-pvst\n", ""
    )
    assert configparse.parse(text).vendor is configparse.Vendor.ARISTA
    [source] = _sources_of(db_session, text, "STP のモードを決めていません")
    assert source == defaults.stp_mode_source("arista")
    assert "MSTP" in source


def test_a_missing_stp_mode_on_nxos_names_the_nexus_default(db_session):
    """出典: Cisco Nexus 9000 Series NX-OS Layer 2 Switching Configuration Guide, Release 10.2(x)
    「Configuring Rapid PVST+」の Default Settings（Spanning Tree mode: Rapid PVST+）。
    """
    text = (
        "hostname nx-a1\nfeature lacp\nfeature interface-vlan\n"
        "interface Ethernet1/1\n  switchport\n  switchport access vlan 10\n"
        "  spanning-tree port type edge\n"
    )
    assert configparse.parse(text).vendor is configparse.Vendor.CISCO_NXOS
    [source] = _sources_of(db_session, text, "STP のモードを決めていません")
    assert source == defaults.stp_mode_source("cisco_nxos")
    assert "Nexus" in source


def test_an_unverified_vendor_gets_no_stp_mode_source():
    """確かめていない機種には、他社の資料を出さない。"""
    assert defaults.stp_mode_source("fortinet") is None
    assert defaults.stp_mode_source(None) is None


@pytest.mark.parametrize(
    "protocol",
    [
        "router eigrp 10\n network 10.0.0.0\n",
        "router rip\n version 2\n network 10.0.0.0\n",
    ],
    ids=["eigrp", "rip"],
)
def test_a_dynamic_protocol_may_bring_the_default_route(db_session, protocol):
    """OSPF・BGP と同じく、EIGRP・RIP も既定の経路を受け取れる。書いていないとは言えない。"""
    text = "hostname r-dyn\nip route 10.9.0.0 255.255.0.0 10.0.0.1\n" + protocol
    assert not [
        t for t in _titles(configcheck.check(db_session, text)) if "既定の経路が見当たり" in t
    ]


def test_an_eos_port_without_a_mode_is_not_said_to_become_a_trunk(db_session):
    """EOS の口の既定は static access で、switchport mode に動的な形が無い。

    出典: Arista EOS User Manual「Virtual LANs (VLANs)」（Ethernet and port channel interfaces are
    configured as switched ports by default。表示例は Administrative Mode: static access。
    switchport mode は access・trunk・dot1q-tunnel で、DTP の記載が無い）。
    """
    text = (
        "! device: sw-a1 (DCS-7050SX3-48YC8, EOS-4.29.2F)\nhostname sw-a1\n"
        "interface Ethernet1\n   switchport access vlan 10\n"
    )
    titles = _titles(configcheck.check(db_session, text))
    assert not [t for t in titles if "相手次第でトランク" in t]


def test_the_negotiable_port_finding_names_its_source(db_session):
    text = "hostname sw-neg\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n"
    assert _sources_of(db_session, text, "相手次第でトランク") == [defaults.DTP_DYNAMIC_AUTO_SOURCE]


ALLIED_FIXTURE = Path(__file__).parent / "fixtures" / "allied-x530-01.txt"


def test_allied_default_native_vlan_needs_no_place_in_the_allowed_list(db_session):
    """AlliedWare Plus は、既定のタグ無し VLAN 1 を許可の一覧に入れなくても一員になる。

    出典: Allied Telesis「VLAN Feature Overview and Configuration Guide」C613-22001-00 REV P の
    Setting native VLAN in trunk mode（By default, a Trunk mode switch port's native VLAN … is
    VLAN 1. … If required, you can change the native VLAN from the default. The new native VLAN must
    already be allowed for this switch port.）。fixture は `allowed vlan add 10,20` と
    `native vlan 1` の組。
    """
    text = ALLIED_FIXTURE.read_text()
    assert configparse.parse(text).vendor is configparse.Vendor.ALLIED
    titles = _titles(configcheck.check(db_session, text))
    assert not [t for t in titles if "タグ無しの VLAN を、許可の一覧から外して" in t]


# 出典: Allied Telesis「STP Feature Overview and Configuration Guide」C613-22026-00 REV A
# （Step 4. Configure BPDU Guard: awplus(config)# spanning-tree portfast bpdu-guard — If required,
# enable the BPDU Guard feature. / By default, the device operates in RSTP mode.）


def test_allied_global_bpdu_guard_covers_the_edge_ports(db_session):
    text = ALLIED_FIXTURE.read_text() + "spanning-tree portfast bpdu-guard\n"
    titles = _titles(configcheck.check(db_session, text))
    assert not [t for t in titles if "BPDU guard が入っていません" in t]


def test_allied_missing_stp_mode_names_the_rstp_default(db_session):
    [source] = _sources_of(db_session, ALLIED_FIXTURE.read_text(), "STP のモードを決めていません")
    assert source == defaults.stp_mode_source("allied")
    assert "RSTP" in source


# 出典: Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9500）「Port Security」の設定例
# （set the total maximum number of secure addresses to 20 (10 for data VLAN and 10 for voice VLAN):
# switchport port-security maximum 20 / maximum 10 vlan access / maximum 10 vlan voice）。
# 口全体の上限は `maximum N` の行で、書かなければ既定の1。


def test_per_vlan_maximums_alone_leave_the_port_total_at_one(db_session):
    text = VOICE_PORT.replace(
        " switchport port-security violation restrict\n",
        " switchport port-security maximum 1 vlan access\n"
        " switchport port-security maximum 1 vlan voice\n"
        " switchport port-security violation restrict\n",
    )
    finding = next(
        f
        for f in configcheck.check(db_session, text).findings
        if "覚えられる数が足りません" in f.title
    )
    assert finding.sources == [defaults.PORT_SECURITY_MAXIMUM_SOURCE]


def test_a_port_total_of_two_with_per_vlan_lines_is_enough(db_session):
    text = VOICE_PORT.replace(
        " switchport port-security violation restrict\n",
        " switchport port-security maximum 1 vlan voice\n"
        " switchport port-security maximum 2\n"
        " switchport port-security violation restrict\n",
    )
    titles = _titles(configcheck.check(db_session, text))
    assert not [t for t in titles if "覚えられる数が足りません" in t]


def test_snooping_without_a_vlan_names_its_source(db_session):
    text = "hostname sw-sv1\nip dhcp snooping\n"
    assert _sources_of(db_session, text, "どの VLAN にも掛けていません") == [
        defaults.DHCP_SNOOPING_VLAN_SOURCE
    ]


def test_arp_inspection_without_a_basis_names_its_source(db_session):
    text = "hostname sw-ai9\nip arp inspection vlan 10\n"
    assert _sources_of(db_session, text, "判断のもとがありません") == [
        defaults.ARP_INSPECTION_BINDING_SOURCE
    ]


def test_junos_logical_systems_are_said_to_be_unread(db_session):
    """論理システムは自分の口と経路表を持つ。読まずに黙ると、その中が「無い」ことになる。

    出典: Juniper「Understanding Logical Systems for Routers and Switches」（Logical systems … have
    their own unique routing tables, interfaces, policies, and routing instances.）。
    """
    text = (
        "set system host-name mx-l1\n"
        "set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24\n"
        "set logical-systems LS1 interfaces ge-0/0/1 unit 0 family inet address 10.1.0.1/24\n"
        "set logical-systems LS1 routing-options static route 0.0.0.0/0 next-hop 10.1.0.254\n"
    )
    result = configcheck.check(db_session, text)
    assert any("論理システム" in note for note in result.notes), result.notes


def test_junos_without_logical_systems_gets_no_such_note(db_session):
    text = (
        "set system host-name mx-l2\n"
        "set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24\n"
    )
    result = configcheck.check(db_session, text)
    assert not any("論理システム" in note for note in result.notes)


def test_a_missing_stp_mode_on_aos_cx_names_the_mstp_default(db_session):
    """出典: AOS-CX 10.12 Layer-2 Bridging Guide（8400 Switch Series）の spanning-tree mode
    （「The no form of this command sets the spanning tree mode to the default mstp.」）。
    """
    text = (
        "!Version ArubaOS-CX GL.10.12.1000\nhostname cx-a1\nspanning-tree\n"
        "interface 1/1/1\n    no shutdown\n    vlan access 10\n"
        "    spanning-tree port-type admin-edge\n"
    )
    assert configparse.parse(text).vendor is configparse.Vendor.ARUBA_CX
    [source] = _sources_of(db_session, text, "STP のモードを決めていません")
    assert source == defaults.stp_mode_source("aruba_cx")
    assert "mstp" in source


@pytest.mark.parametrize(
    ("text", "vendor", "word"),
    [
        (
            # 出典: アラクサラ「AX3660S コンフィグレーションガイド Vol.1」Ver.12.1
            # 27.2.2 動作モードの設定
            # （動作モードを設定しない場合，pvst モードで動作します）
            'hostname "ax-s1"\n! AX3660S\ninterface gigabitethernet 1/0/1\n'
            " switchport mode access\n spanning-tree portfast\n",
            "alaxala",
            "pvst",
        ),
        (
            # 出典: Dell SmartFabric OS10 User Guide Release 10.6.0「Enable Rapid-PVST」
            # （By default, Rapid-PVST is enabled and creates an instance during VLAN creation.）
            "hostname os10-a1\ninterface ethernet 1/1/1\n switchport access vlan 10\n"
            " spanning-tree port type edge\n",
            "dell_os10",
            "Rapid-PVST",
        ),
        (
            # 出典: Cisco Business Switches 350 Series CLI Guide「Spanning Tree Commands」の
            # spanning-tree mode（Default Configuration: The default is RSTP.）
            "config-file-header\nsb-a1\nv3.0.0.69 / RCBS3.0.0.69\n"
            "interface gi1/0/1\n spanning-tree portfast\n",
            "cisco_sb",
            "RSTP",
        ),
        (
            # 出典: HPE「Aruba 2930F / 2930M Advanced Traffic Management Guide for AOS-S 16.11」の
            # Setting spanning tree to operate in 802.1D legacy mode（Default: MSTP-operation.
            # The no form of this command returns the switch to the default 802.1s native mode）
            "; JL253A Configuration Editor; Created on release #WC.16.11.0001\n"
            'hostname "sw-aoss-01"\n'
            "vlan 1\n   untagged 1-24\n   exit\n"
            "spanning-tree\n"
            "spanning-tree 1-20 admin-edge-port\n",
            "aruba_os",
            "MSTP",
        ),
    ],
)
def test_a_missing_stp_mode_names_the_vendor_default(db_session, text, vendor, word):
    """STP のモードを決めていないとき、その機種の資料で確かめた既定を出す。"""
    assert configparse.parse(text).vendor.value == vendor
    [source] = _sources_of(db_session, text, "STP のモードを決めていません")
    assert source == defaults.stp_mode_source(vendor)
    assert word in source


_STP_WORD_PORT = (
    "interface GigabitEthernet1/0/1\n port link-type access\n stp edged-port enable\n#\n"
)


@pytest.mark.parametrize(
    ("head", "vendor", "word"),
    [
        (
            # 出典: Huawei「S9300 and S9300E V200R008 Configuration Guide - Ethernet Switching」の
            # Configuring Basic MSTP Functions（By default, the working mode is MSTP.）
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)\nsysname hw-s1\n#\n",
            "huawei",
            "MSTP",
        ),
        (
            # 出典: H3C「07-Spanning tree configuration」Setting the spanning tree mode
            # （The default setting is the MSTP mode.）
            "H3C Comware Software, Version 7.1.064\nsysname h3c-s1\n#\n",
            "h3c",
            "MSTP",
        ),
        (
            # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.1.31 stp mode
            # （デフォルト mstp）
            "# QX-S1108GT-2G\nsysname qx-s1\n#\n",
            "nec_qx",
            "mstp",
        ),
    ],
)
def test_a_missing_stp_mode_on_undo_style_vendors(db_session, head, vendor, word):
    """`stp …` で書く機種でも、モードを決めていなければ同じ指摘を出す。

    これまでは `spanning-tree` の行だけを見ていたため、この3機種では黙って通っていた。
    """
    text = head + _STP_WORD_PORT
    assert configparse.parse(text).vendor.value == vendor
    [source] = _sources_of(db_session, text, "STP のモードを決めていません")
    assert source == defaults.stp_mode_source(vendor)
    assert word in source


@pytest.mark.parametrize(
    "extra", ["stp mode rstp\n#\n", "stp global disable\n#\n", "stp disable\n#\n"]
)
def test_undo_style_stp_mode_or_global_off_gets_no_mode_finding(db_session, extra):
    """モードを書いた機器と、装置全体で STP を止めた機器には出さない（新しい分岐の見張り）。"""
    text = (
        "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)\nsysname hw-s2\n#\n"
        + extra
        + _STP_WORD_PORT
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "STP のモードを決めていません" not in titles


_VRP_HEAD = "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)\nsysname hw-e1\n#\n"


def test_vrp_edged_port_default_without_bpdu_protection_is_found(db_session):
    """`stp edged-port default` は全部の口を端末口にする。BPDU protection が無ければ指摘する。

    出典: Huawei「Fat AP and Cloud AP V200R019C00 Command Reference」の stp edged-port default
    （After the stp edged-port default command is run on a device, all ports of the device will be
    become edge ports）。`stp edged-port disable` と書いた口は外れる（「S220, S310, S530, and S620
    V600R025C00 Configuration Guide - Ethernet Switching」Configuring Edge Ports and BPDU Filtering
    Ports）。
    """
    text = (
        _VRP_HEAD
        + "stp mode rstp\nstp edged-port default\n#\n"
        + "interface GigabitEthernet0/0/1\n port link-type access\n#\n"
        + "interface GigabitEthernet0/0/2\n port link-type trunk\n stp edged-port disable\n#\n"
    )
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "端末用の口に、BPDU guard が入っていません"
    ]
    assert finding.evidence == ["GigabitEthernet0/0/1: 端末用の口の指定のみ"]


def test_vrp_edged_port_default_with_global_bpdu_protection_is_quiet(db_session):
    """全体の `stp bpdu-protection` で守られていれば出さない（新しい分岐の見張り）。"""
    text = (
        _VRP_HEAD
        + "stp mode rstp\nstp edged-port default\nstp bpdu-protection\n#\n"
        + "interface GigabitEthernet0/0/1\n port link-type access\n#\n"
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "端末用の口に、BPDU guard が入っていません" not in titles


def test_vrp_port_bpdu_filter_is_found(db_session):
    """VRP の口ごとの `stp bpdu-filter enable` も、その口の STP を止めるのと同じ。

    出典: Huawei「NetEngine AR6700V-L V600R024C00 Command Reference」の stp bpdu-filter
    （If the stp bpdu-filter enable command is run on a port, the port does not process or send
    BPDUs. The port cannot negotiate the STP status with the directly connected port on the peer
    device.）
    """
    text = (
        _VRP_HEAD
        + "stp mode rstp\n#\n"
        + "interface GigabitEthernet0/0/3\n port link-type trunk\n stp bpdu-filter enable\n#\n"
    )
    [finding] = [
        f for f in configcheck.check(db_session, text).findings if "BPDU フィルタ" in f.title
    ]
    assert finding.level == "high"
    assert finding.evidence == ["GigabitEthernet0/0/3: stp bpdu-filter enable（trunk の口）"]


def test_port_bpdu_protection_disable_overrides_global_guard(db_session):
    """全体で BPDU 保護を入れていても、口で `stp port bpdu-protection disable` にした端末口は
    守られない。

    出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.1.35 stp port bpdu-protection
    （インタフェースの BPDU ガード機能の状態はグローバルでの BPDU ガード機能と同一。
    disable: BPDU ガード機能を無効にします）。
    H3C「07-Spanning tree commands」にも同じコマンドがある。
    """
    text = (
        "# QX-S1108GT-2G\nsysname qx-g1\n#\nstp mode rstp\nstp bpdu-protection\n#\n"
        "interface GigabitEthernet1/0/1\n port link-type access\n stp edged-port\n#\n"
        "interface GigabitEthernet1/0/2\n port link-type access\n stp edged-port\n"
        " stp port bpdu-protection disable\n#\n"
    )
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "端末用の口に、BPDU guard が入っていません"
    ]
    assert finding.evidence == ["GigabitEthernet1/0/2: 端末用の口の指定のみ"]


def test_vrp_bpdu_filter_default_is_found(db_session):
    """VRP の全体の `stp bpdu-filter default` は、`stp bpdu-filter disable` の口以外を
    BPDU フィルタにする。

    出典: Huawei「NetEngine AR6700V-L V600R024C00 Command Reference」の stp bpdu-filter
    （If a port has been configured as a non-BPDU filter port using the stp bpdu-filter
    disable command, it remains a non-BPDU filter port, even if the stp bpdu-filter default
    command is run）。
    """
    text = (
        _VRP_HEAD
        + "stp mode rstp\nstp bpdu-filter default\n#\n"
        + "interface GigabitEthernet0/0/1\n port link-type access\n#\n"
        + "interface GigabitEthernet0/0/2\n port link-type trunk\n stp bpdu-filter disable\n#\n"
    )
    [finding] = [
        f for f in configcheck.check(db_session, text).findings if "BPDU フィルタ" in f.title
    ]
    assert finding.evidence == ["GigabitEthernet0/0/1: stp bpdu-filter default（全体）"]


_UNDO_PORT = "interface GigabitEthernet1/0/1\n port link-type access\n#\n"


@pytest.mark.parametrize(
    ("head", "vendor", "word"),
    [
        (
            # 出典: Huawei「S5700 and S6700 V200R025C00 Command Reference」の dhcp snooping
            # trusted（By default, all interfaces are untrusted interfaces）
            _VRP_HEAD + "dhcp enable\ndhcp snooping enable\n#\nvlan 10\n dhcp snooping enable\n#\n",
            "huawei",
            "dhcp snooping trusted",
        ),
        (
            # 出典: H3C「DHCP snooping configuration」（If you enable DHCP snooping globally, DHCP
            # snooping is enabled on all interfaces / all ports are untrusted ports after DHCP
            # snooping is enabled）
            "H3C Comware Software, Version 7.1.064\nsysname h3c-d1\n#\ndhcp snooping enable\n#\n",
            "h3c",
            "dhcp snooping trust",
        ),
        (
            # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」7.1.2 dhcp snooping enable
            # （トラステッドポートを設定する前は、DHCP サーバからのすべての応答を廃棄）
            "# QX-S1108GT-2G\nsysname qx-d1\n#\ndhcp snooping enable\n#\n",
            "nec_qx",
            "dhcp snooping trust",
        ),
    ],
)
def test_undo_style_dhcp_snooping_without_trusted_port(db_session, head, vendor, word):
    """`dhcp snooping enable` で書く機種でも、信用する口が無ければ指摘する。"""
    text = head + _UNDO_PORT
    assert configparse.parse(text).vendor.value == vendor
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "DHCP の見張りで、信用する口を1つも決めていません"
    ]
    assert finding.evidence == [f"dhcp snooping enable はあるが {word} が無い"]
    assert finding.sources == [defaults.DHCP_SNOOPING_UNTRUSTED_SOURCES[vendor]]


def test_huawei_dhcp_snooping_global_only_is_not_on_any_vlan(db_session):
    """Huawei は全体の `dhcp snooping enable` だけでは働かない。VLAN か口でも有効にする。

    出典: Huawei「S5700 and S6700 V200R025C00 Command Reference」の dhcp snooping enable
    （You must enable DHCP snooping in the system view before enabling DHCP snooping on an
    interface, in a BD, or in a VLAN）。
    """
    text = _VRP_HEAD + "dhcp enable\ndhcp snooping enable\n#\n" + _UNDO_PORT
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "DHCP の見張りを、どの VLAN にも掛けていません" in titles


@pytest.mark.parametrize(
    "text",
    [
        _VRP_HEAD
        + "dhcp snooping enable\ndhcp snooping enable vlan 10\n#\n"
        + "interface GigabitEthernet0/0/24\n port link-type trunk\n dhcp snooping trusted\n#\n",
        "# QX-S1108GT-2G\nsysname qx-d2\n#\ndhcp snooping enable\n#\n"
        "interface GigabitEthernet1/0/8\n port link-type trunk\n dhcp snooping trust\n#\n",
    ],
)
def test_undo_style_dhcp_snooping_with_trusted_port_is_quiet(db_session, text):
    """信用する口を決めてあれば出さない（新しい分岐の見張り）。"""
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert not any("DHCP の見張り" in t for t in titles)


@pytest.mark.parametrize(
    ("head", "vendor"),
    [
        # 出典: H3C「20-ARP attack protection configuration」（arp detection trust: By default, an
        # interface is untrusted / If no match is found, the device discards the ARP packet）
        ("H3C Comware Software, Version 7.1.064\nsysname h3c-a1\n#\n", "h3c"),
        # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.6.1 arp detection enable、
        # 8.6.3 arp detection trust（既定は非 ARP トラステッドポート）
        ("# QX-S1108GT-2G\nsysname qx-a1\n#\n", "nec_qx"),
    ],
)
def test_arp_detection_without_trusted_port(db_session, head, vendor):
    """H3C・NEC QX の ARP の見張りで、信用する口が1つも無ければ指摘する。"""
    text = head + "dhcp snooping enable\n#\nvlan 10\n arp detection enable\n#\n" + _UNDO_PORT
    assert configparse.parse(text).vendor.value == vendor
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "ARP の見張りで、信用する口を1つも決めていません"
    ]
    assert finding.sources == [defaults.ARP_DETECTION_UNTRUSTED_SOURCES[vendor]]


def test_arp_detection_with_trusted_port_is_quiet(db_session):
    """上位の口を信用していれば出さない（新しい分岐の見張り）。"""
    text = (
        "# QX-S1108GT-2G\nsysname qx-a2\n#\nvlan 10\n arp detection enable\n#\n"
        "interface GigabitEthernet1/0/8\n port link-type trunk\n arp detection trust\n#\n"
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert not any("ARP の見張り" in t for t in titles)


def test_huawei_dai_without_binding_basis(db_session):
    """Huawei の DAI は、DHCP の見張りも静的な対応表も無いと全部の ARP を捨てる。

    出典: Huawei「S300, S500, S2700, S5700, and S6700 V200R024C00 Configuration Guide - Security」
    の Configuring DAI（If the ARP packet matches no binding entry, the device considers the ARP
    packet invalid and discards the packet）。
    """
    text = _VRP_HEAD + "vlan 10\n arp anti-attack check user-bind enable\n#\n" + _UNDO_PORT
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "ARP の見張りに、判断のもとがありません"
    ]
    assert finding.sources == [defaults.HUAWEI_DAI_BINDING_SOURCE]


def test_huawei_dai_with_static_binding_is_quiet(db_session):
    """静的な対応表（user-bind static）があれば、判断のもとはある（新しい分岐の見張り）。"""
    text = (
        _VRP_HEAD
        + "user-bind static ip-address 10.0.0.5 mac-address 0000-1111-2222\n#\n"
        + "vlan 10\n arp anti-attack check user-bind enable\n#\n"
        + _UNDO_PORT
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "ARP の見張りに、判断のもとがありません" not in titles


def test_huawei_port_security_protect_action_protect_is_silent(db_session):
    """Huawei の `port-security protect-action protect` は、超えた分を記録なしで捨てる。

    出典: Huawei「S1720, S2700, S5700, and S6720 V200R011C10 Command Reference」の
    port-security protect-action（The default action is restrict. / protect: Discards packets with
    new source MAC addresses / restrict: … and sends a trap message）。
    """
    text = (
        _VRP_HEAD
        + "interface GigabitEthernet0/0/5\n port link-type access\n port-security enable\n"
        + " port-security protect-action protect\n#\n"
    )
    [finding] = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == "ポートセキュリティが、黙って落とす設定になっています"
    ]
    assert finding.evidence == ["GigabitEthernet0/0/5: port-security protect-action protect"]


def test_huawei_port_security_default_action_is_quiet(db_session):
    """protect-action を書かなければ既定の restrict（記録が残る）。指摘しない（見張り）。"""
    text = (
        _VRP_HEAD
        + "interface GigabitEthernet0/0/5\n port link-type access\n port-security enable\n#\n"
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert not any("ポートセキュリティ" in t for t in titles)


@pytest.mark.parametrize(
    "line",
    [
        "snmp-agent community read simple public",
        "snmp-agent community write simple private",
        "snmp-agent community simple public user-role network-operator",
    ],
)
def test_h3c_snmp_default_community_with_simple_keyword(db_session, line):
    """H3C は名前の前に simple を書ける。既定の合言葉はその形でも見つける。

    出典: H3C「02-SNMP commands」の snmp-agent community（VACM mode: { read | write }
    [ simple | cipher ] community-name、RBAC mode: [ simple | cipher ] community-name
    user-role role-name）。
    """
    text = "H3C Comware Software, Version 7.1.064\nsysname h3c-s1\n#\n" + line + "\n#\n"
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "SNMP の合言葉が既定のままです" in titles


def test_h3c_snmp_cipher_community_is_not_read_as_default(db_session):
    """cipher の後ろは暗号化した名前。既定の合言葉とは言わない（見張り）。"""
    text = (
        "H3C Comware Software, Version 7.1.064\nsysname h3c-s2\n#\n"
        "snmp-agent community read cipher $c$3$abcdefpublic\n#\n"
    )
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "SNMP の合言葉が既定のままです" not in titles


_QX_HEAD = "# QX-S1108GT-2G\nsysname qx-l1\n#\n"


@pytest.mark.parametrize(
    ("body", "title"),
    [
        # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.16 idle-timeout
        # （タイムアウト時間を 0 にすると、セッションがタイムアウトしません）
        ("line vty 0 63\n idle-timeout 0 0\n#\n", "入ったままの画面が、いつまでも閉じません"),
        ("line vty 0 63\n idle-timeout 0\n#\n", "入ったままの画面が、いつまでも閉じません"),
        # 出典: 同 6.1.8 ssh server compatible-ssh1x enable（SSH1 クライアントをサポート）
        ("ssh server compatible-ssh1x enable\n#\n", "SSH が古い方式のままです"),
    ],
)
def test_qx_login_lines_are_read(db_session, body, title):
    """NEC QX の時間切れなし・SSH1 互換の書き方も見つける。"""
    titles = [f.title for f in configcheck.check(db_session, _QX_HEAD + body).findings]
    assert title in titles


def test_qx_idle_timeout_with_seconds_is_not_unlimited(db_session):
    """`idle-timeout 0 30` は 30 秒で切れる。時間切れなしとは言わない（見張り）。"""
    text = _QX_HEAD + "line vty 0 63\n idle-timeout 0 30\n#\n"
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert "入ったままの画面が、いつまでも閉じません" not in titles


@pytest.mark.parametrize(
    ("head", "line"),
    [
        # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.18 ip http enable
        # （既定は無効）
        (_QX_HEAD, "ip http enable"),
        # 出典: Huawei「S1720, S2700, S5700, and S6720 V200R011C10 Command Reference」の
        # http server enable
        (_VRP_HEAD, "http server enable"),
        (_VRP_HEAD, "http ipv6 server enable"),
    ],
)
def test_undo_style_http_server_is_found(db_session, head, line):
    """NEC QX・Huawei で暗号化しない管理用 web を開く行も見つける。"""
    titles = [f.title for f in configcheck.check(db_session, head + line + "\n#\n").findings]
    assert "管理用の web が開いています" in titles


@pytest.mark.parametrize(
    ("head", "line"),
    [(_QX_HEAD, "ip https enable"), (_VRP_HEAD, "http secure-server enable")],
)
def test_undo_style_https_server_is_quiet(db_session, head, line):
    """https は暗号化されるので言わない（見張り）。"""
    titles = [f.title for f in configcheck.check(db_session, head + line + "\n#\n").findings]
    assert "管理用の web が開いています" not in titles


_NO_AUTH_TITLE = "機器に入る口で、認証を求めていません"


def test_qx_line_without_authentication_is_found(db_session):
    """`line vty` の下の `authentication-mode none` は、合言葉なしで入れる口として言う。

    出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.1 authentication-mode
    （none: 認証を無効にします。User line view）。
    """
    text = _QX_HEAD + "line vty 0 63\n authentication-mode none\n#\n"
    found = [f for f in configcheck.check(db_session, text).findings if f.title == _NO_AUTH_TITLE]
    assert found and found[0].level == HIGH
    assert found[0].evidence == ["line vty 0 63: authentication-mode none"]


@pytest.mark.parametrize(
    "body",
    [
        "line vty 0 63\n authentication-mode password\n#\n",
        "line vty 0 63\n authentication-mode scheme\n#\n",
        # AAA の認証方式の none は口の設定ではない（Huawei の aaa の下の書き方）
        "aaa\n authentication-scheme abc\n  authentication-mode none\n#\n",
    ],
)
def test_login_authentication_quiet_cases(db_session, body):
    """認証ありの口や、口のまとまりの外の none は言わない（見張り）。"""
    titles = [f.title for f in configcheck.check(db_session, _QX_HEAD + body).findings]
    assert _NO_AUTH_TITLE not in titles


_FTP_TITLE = "FTP サーバーが開いています"


def test_qx_ftp_server_is_found(db_session):
    """NEC QX の `ftp server enable` は、暗号化しない FTP を開く行として言う。

    出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」4.1.8 ftp server enable（既定は無効）。
    """
    found = [
        f
        for f in configcheck.check(db_session, _QX_HEAD + "ftp server enable\n#\n").findings
        if f.title == _FTP_TITLE
    ]
    assert found and found[0].level == HIGH


@pytest.mark.parametrize("line", ["sftp server enable", "undo ftp server enable"])
def test_sftp_and_disabled_ftp_are_quiet(db_session, line):
    """SFTP（6.1.3 sftp server enable）と止めた形は言わない（見張り）。"""
    titles = [f.title for f in configcheck.check(db_session, _QX_HEAD + line + "\n#\n").findings]
    assert _FTP_TITLE not in titles


_DIRECTED_BROADCAST_TITLE = "外から、その網の全員あての通信を送り込めます"
_IOS_SVI = "hostname r-db1\ninterface Vlan10\n ip address 192.0.2.1 255.255.255.0\n"


def test_ios_directed_broadcast_is_found(db_session):
    """ACL を付けない `ip directed-broadcast` は、網あての一斉送信を広げる行として言う。

    出典: Cisco「Cisco IOS IP Application Services Command Reference」の ip directed-broadcast
    （既定は無効。ICMP の一斉送信が悪用されてきたので、要らない口では止めることを勧めている）。
    """
    text = _IOS_SVI + " ip directed-broadcast\n!\n"
    found = [
        f
        for f in configcheck.check(db_session, text).findings
        if f.title == _DIRECTED_BROADCAST_TITLE
    ]
    assert found and found[0].level == MEDIUM


@pytest.mark.parametrize("line", ["ip directed-broadcast 110", "no ip directed-broadcast"])
def test_directed_broadcast_with_acl_or_disabled_is_quiet(db_session, line):
    """ACL で絞った形と止めた形は言わない（見張り）。"""
    text = _IOS_SVI + f" {line}\n!\n"
    titles = [f.title for f in configcheck.check(db_session, text).findings]
    assert _DIRECTED_BROADCAST_TITLE not in titles


@pytest.mark.parametrize(
    ("line", "title", "fix"),
    [
        # 出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.41 telnet server enable・
        # 2.1.29 protocol inbound
        ("telnet server enable", "telnet で入れる状態です", "undo telnet server enable"),
        # 出典: 同 2.1.18 ip http enable
        ("ip http enable", "管理用の web が開いています", "undo ip http enable"),
        # 出典: 同 4.1.14 snmp-agent community { read | write }
        (
            "snmp-agent community write simple n3tw0rk",
            "SNMP から設定を書き換えられます",
            "snmp-agent community read",
        ),
        # 出典: 同 6.1.8 ssh server compatible-ssh1x enable（undo の形に enable は付かない）
        (
            "ssh server compatible-ssh1x enable",
            "SSH が古い方式のままです",
            "undo ssh server compatible-ssh1x",
        ),
        # 出典: 同 7 章 1.2.8 password（装置管理ユーザは hash か simple）
        (
            "local-user admin class manage\n password simple Adm1nPass",
            "利用者のパスワードが、元に戻せる形で入っています",
            "password hash",
        ),
    ],
)
def test_qx_findings_show_the_qx_way_to_fix(db_session, line, title, fix):
    """NEC QX の Config には、Cisco の書き方ではなく QX で打てる直し方を出す。"""
    text = _QX_HEAD + line + "\n#\n"
    finding = next(f for f in configcheck.check(db_session, text).findings if f.title == title)
    assert fix in finding.why
    assert "transport input" not in finding.why and "no ip http server" not in finding.why
    assert "version 2" not in finding.why and "secret" not in finding.why


def test_ios_findings_keep_the_ios_way_to_fix(db_session):
    """Cisco の Config では今までどおり Cisco の書き方を出す（見張り）。"""
    text = "hostname r-fix1\nline vty 0 4\n transport input telnet\n!\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "transport input ssh" in finding.why


@pytest.mark.parametrize(
    ("line", "title", "fix"),
    [
        # 出典: Juniper「Day One: Juniper Ambassadors' Cookbook for Enterprise」の初期設定の例
        ("set system services telnet", "telnet で入れる状態です", "delete system services telnet"),
        (
            "set system services web-management http",
            "管理用の web が開いています",
            "delete system services web-management http",
        ),
        # 出典: Juniper「Junos OS CLI Reference」の community (SNMP)
        (
            "set snmp community n3tw0rk authorization read-write",
            "SNMP から設定を書き換えられます",
            "authorization read-only",
        ),
    ],
)
def test_junos_findings_show_the_junos_way_to_fix(db_session, line, title, fix):
    """Junos の Config には、Cisco の書き方ではなく Junos で打てる直し方を出す。"""
    text = "set system host-name rt-fix\n" + line + "\n"
    finding = next(f for f in configcheck.check(db_session, text).findings if f.title == title)
    assert fix in finding.why
    assert "transport input" not in finding.why and "no ip http server" not in finding.why
    assert "RO で足ります" not in finding.why


@pytest.mark.parametrize(
    ("access", "title", "fix"),
    [
        # 出典: Fortinet Community「Technical Tip: How to meet the requirements for FSBP SH01.1 and
        # SH01.2」（unselect allowaccess http telnet）
        ("ping https ssh telnet", "telnet で入れる状態です", "unselect allowaccess telnet"),
        ("ping http https ssh", "管理用の web が開いています", "unselect allowaccess http"),
    ],
)
def test_fortigate_findings_show_the_fortigate_way_to_fix(db_session, access, title, fix):
    """FortiGate の Config には、Cisco の書き方ではなく FortiOS で打てる直し方を出す。"""
    text = (
        'config system global\n    set hostname "fw-fix"\nend\n'
        "config system interface\n"
        '    edit "port1"\n'
        f"        set allowaccess {access}\n"
        "    next\nend\n"
    )
    finding = next(f for f in configcheck.check(db_session, text).findings if f.title == title)
    assert fix in finding.why
    assert "transport input" not in finding.why and "no ip http server" not in finding.why


@pytest.mark.parametrize(
    ("text", "fix"),
    [
        # 出典: ヤマハ「RTX コマンドリファレンス」4 章「TELNET サーバー機能の ON/OFF の設定」
        ("# RTX830 Rev.15.02.28\ntelnetd service on\n", "telnetd service off"),
        # 出典: ヤマハ「TELNET複数セッション機能、SSHサーバー機能」の SSH サーバー機能の設定
        ("# RTX830 Rev.15.02.28\ntelnetd service on\n", "sshd host key generate"),
        # 出典: Allied Telesis「AlliedWare Plus Best Practice Guide」の Telnet の節
        (
            "! Allied Telesis AlliedWare Plus (tm) version 5.5.2\nhostname sw\nservice telnet\n",
            "no service telnet",
        ),
    ],
)
def test_yamaha_and_allied_telnet_show_their_way_to_fix(db_session, text, fix):
    """ヤマハ・アライドの telnet の指摘には、その機器で打てる直し方を出す。"""
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert fix in finding.why
    assert "transport input" not in finding.why


def test_h3c_telnet_shows_the_h3c_way_to_fix(db_session):
    """H3C の telnet の指摘には、Comware で打てる直し方を出す。

    出典: H3C「Fundamentals Command Reference」03-Login management commands の
    telnet server enable（undo で止める）と protocol inbound { all | ssh | telnet }。
    """
    text = "H3C Comware Software, Version 7.1.064\nsysname h3c-t1\n#\ntelnet server enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "undo telnet server enable" in finding.why
    assert "transport input" not in finding.why


@pytest.mark.parametrize(
    ("line", "title", "fix"),
    [
        # 出典: H3C「Fundamentals Command Reference」03-Login management commands の ip http enable
        ("ip http enable", "管理用の web が開いています", "undo ip http enable"),
        # 出典: H3C「02-SNMP commands」の snmp-agent community { read | write }
        (
            "snmp-agent community write simple n3tw0rk",
            "SNMP から設定を書き換えられます",
            "snmp-agent community read",
        ),
        # 出典: H3C「SSH commands」の ssh server compatible-ssh1x enable
        (
            "ssh server compatible-ssh1x enable",
            "SSH が古い方式のままです",
            "undo ssh server compatible-ssh1x",
        ),
    ],
)
def test_h3c_http_and_snmp_show_the_h3c_way_to_fix(db_session, line, title, fix):
    """H3C の http・SNMP の書き込みの指摘にも、Comware で打てる直し方を出す。"""
    text = "H3C Comware Software, Version 7.1.064\nsysname h3c-t2\n#\n" + line + "\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == title)
    assert fix in finding.why
    assert "no ip http server" not in finding.why and "RO で足ります" not in finding.why
    assert "version 2 にしてください" not in finding.why


@pytest.mark.parametrize(
    "body",
    ["telnet server enable\n#\n", "user-interface vty 0 4\n protocol inbound telnet\n#\n"],
)
def test_huawei_telnet_shows_the_vrp_way_to_fix(db_session, body):
    """Huawei の telnet の指摘には、VRP で打てる直し方を出す。

    出典: Huawei「S Series Campus Switches Troubleshooting Guide (V100 and V200)」の
    SSH/Telnet Attack（undo telnet server enable）と FAQs About Basic Configuration
    （user-interface vty の protocol inbound）。
    """
    findings = configcheck.check(db_session, _VRP_HEAD + body).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "undo telnet server enable" in finding.why
    assert "user-interface vty" in finding.why
    assert "transport input" not in finding.why


def test_huawei_http_shows_the_vrp_way_to_fix(db_session):
    """Huawei の管理用 web の指摘には、VRP で打てる直し方を出す。

    出典: Huawei「S Series Campus Switches Troubleshooting Guide (V100 and V200)」の
    TCP/UDP Attack（undo http server enable / undo http secure-server enable）。
    """
    findings = configcheck.check(db_session, _VRP_HEAD + "http server enable\n#\n").findings
    finding = next(f for f in findings if f.title == "管理用の web が開いています")
    assert "undo http server enable" in finding.why
    assert "no ip http server" not in finding.why


def test_huawei_snmp_write_shows_the_vrp_way_to_fix(db_session):
    """Huawei の SNMP の書き込みの指摘には、read の community を案内する。

    出典: Huawei「CloudEngine S3700, S5700, and S6700 V600R023C00 Configuration Guide」の
    Configuring Basic SNMPv1 Functions（snmp-agent community { read | write }）。
    """
    text = _VRP_HEAD + "snmp-agent community write cipher %^%#abcdefgh%^%#\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "SNMP から設定を書き換えられます")
    assert "snmp-agent community read" in finding.why
    assert "RO で足ります" not in finding.why


def test_h3c_plain_password_shows_the_h3c_way_to_fix(db_session):
    """H3C の `password simple` の指摘には、password hash を案内する。

    出典: H3C「AAA commands」の password（[ { hash | simple } string ]。
    simple でも保存するとハッシュになる）。
    """
    text = (
        "H3C Comware Software, Version 7.1.064\nsysname h3c-p1\n#\n"
        "local-user admin class manage\n password simple Adm1nPass\n#\n"
    )
    findings = configcheck.check(db_session, text).findings
    title = "利用者のパスワードが、元に戻せる形で入っています"
    finding = next(f for f in findings if f.title == title)
    assert "password hash" in finding.why
    assert "secret" not in finding.why


def test_huawei_ssh1_shows_the_vrp_way_to_fix(db_session):
    """Huawei の SSH1 互換の指摘には、VRP の undo の形（enable 付き）を案内する。

    出典: Huawei「S5700 and S6700 V200R025C00 Command Reference」の
    ssh server compatible-ssh1x enable（undo ssh server compatible-ssh1x enable）。
    """
    text = _VRP_HEAD + "ssh server compatible-ssh1x enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "SSH が古い方式のままです")
    assert "undo ssh server compatible-ssh1x enable" in finding.why
    assert "version 2 にしてください" not in finding.why


def test_asa_telnet_shows_the_asa_way_to_fix(db_session):
    """ASA の telnet の指摘には、IOS の line vty ではなく ASA の書き方を案内する。

    出典: Cisco「Cisco Secure Firewall ASA Series Command Reference, T - Z Commands」の telnet
    （no telnet / clear configure telnet）。
    """
    text = "ASA Version 9.16(4)\nhostname fw\ntelnet 10.0.0.0 255.255.255.0 inside\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "no telnet" in finding.why and "ssh " in finding.why
    assert "transport input" not in finding.why


def test_huawei_reversible_admin_password_is_found(db_session):
    """Huawei で、管理に入る利用者の合言葉が password cipher（元に戻せる形）なら指摘する。

    出典: Huawei「S5700 and S6700 V200R024C10 Configuration Guide - User Access and
    Authentication」の Configuring a Local User（`local-user user-name password
    { cipher | irreversible-cipher } password`。管理者の利用者は元に戻せない形にする）、
    「S300, S500, S2700, S5700, and S6700 V200R024C00 Command Reference」の Password setting
    （3DES・AES は元に戻せる。管理者の合言葉には元に戻せない方式を使う）。
    """
    text = (
        _VRP_HEAD + "aaa\n"
        " local-user admin password cipher %^%#abc%^%#\n"
        " local-user admin service-type ssh terminal\n"
        " local-user dot1x password cipher %^%#def%^%#\n"
        " local-user dot1x service-type 8021x\n"
        " local-user ops password irreversible-cipher $1a$xyz$\n"
        " local-user ops service-type ssh\n"
        "#\n"
    )
    findings = configcheck.check(db_session, text).findings
    hits = [f for f in findings if f.title == "利用者のパスワードが、元に戻せる形で入っています"]
    assert len(hits) == 1
    assert "irreversible-cipher" in hits[0].why
    assert hits[0].evidence == ["local-user admin password cipher"]


def test_huawei_reversible_password_for_web_or_ftp_admin_is_found(db_session):
    """http・ftp・api だけの利用者も管理の種類なので、password cipher なら指摘する。

    出典: Huawei「S7700 V200R023C10 Configuration Guide - User Access and Authentication」の
    Configuring a Local User（Administrative: api, ftp, http, ssh, telnet, x25-pad, and terminal /
    Common: 8021x, bind, ppp and web）。
    """
    text = (
        _VRP_HEAD + "aaa\n"
        " local-user webadm password cipher %^%#abc%^%#\n"
        " local-user webadm service-type http\n"
        " local-user portal password cipher %^%#def%^%#\n"
        " local-user portal service-type web\n"
        "#\n"
    )
    findings = configcheck.check(db_session, text).findings
    hits = [f for f in findings if f.title == "利用者のパスワードが、元に戻せる形で入っています"]
    assert [h.evidence for h in hits] == [["local-user webadm password cipher"]]


def test_huawei_http_ipv6_shows_the_ipv6_way_to_close(db_session):
    """Huawei の http ipv6 server enable には、IPv6 の口を閉じる undo の形も案内する。

    undo http server enable は IPv4 の口しか閉じない。
    出典: Huawei「智简园区网络解决方案 V100R020C10」の LAN网络站点配置
    （http ipv6 server enable / undo http ipv6 server enable、http [ ipv6 ] secure-server enable）。
    """
    text = _VRP_HEAD + "http ipv6 server enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "管理用の web が開いています")
    assert "undo http ipv6 server enable" in finding.why
    assert "http ipv6 secure-server enable" in finding.why


def test_huawei_telnet_ipv6_is_found_with_the_ipv6_way_to_close(db_session):
    """Huawei の telnet ipv6 server enable も telnet の入口として指摘し、IPv6 の閉じ方を出す。

    出典: Huawei「WLAN AC V200R019C00 Command Reference」の telnet server enable
    （telnet [ ipv6 ] server enable / undo telnet [ ipv6 ] server enable）。
    """
    text = _VRP_HEAD + "telnet ipv6 server enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "undo telnet ipv6 server enable" in finding.why


def test_huawei_ftp_ipv6_is_found_with_the_ipv6_way_to_close(db_session):
    """Huawei の ftp ipv6 server enable も FTP の入口として指摘し、IPv6 の閉じ方を出す。

    出典: Huawei「CloudEngine S3700, S5700 and S6700 V600R024C10 Command Reference」の
    ftp server enable（undo ftp server [ enable ]）と ftp ipv6 server enable
    （undo ftp ipv6 server [ enable ]）。
    """
    text = _VRP_HEAD + "ftp ipv6 server enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "FTP サーバーが開いています")
    assert "undo ftp ipv6 server" in finding.why


@pytest.mark.parametrize(
    "head",
    [
        "H3C Comware Software, Version 7.1.064\nsysname h3c-t1\n#\n",
        _VRP_HEAD,
    ],
)
def test_tftp_server_enable_is_found(db_session, head):
    """H3C・Huawei の tftp server enable は、合言葉なしでファイルを読み書きできる口なので指摘する。

    出典: H3C「06-FTP and TFTP commands」の tftp server enable
    （undo tftp server enable、既定は無効）、Huawei「NetEngine AR1000V V300R024 CLI-based
    Configuration Guide - Basic Configuration」の Managing Files When the Device Functions as
    a TFTP Server（使い終わったら undo tftp server enable で止める）。
    """
    findings = configcheck.check(db_session, head + "tftp server enable\n#\n").findings
    finding = next(f for f in findings if f.title == "TFTP サーバーが開いています")
    assert "undo tftp server enable" in finding.why


def test_ios_tftp_server_without_access_list_is_found(db_session):
    """Cisco IOS の tftp-server で、アクセスリストの番号が無い行だけを指摘する。

    出典: Cisco「Cisco IOS Configuration Fundamentals Command Reference」の tftp-server
    （tftp-server flash [partition-number:] filename1 [alias filename2] [access-list-number]）、
    「System Management Configuration Guide, Cisco IOS XE 17.x」の Configuring Basic File
    Transfer Services（番号を付けると、そのアクセスリストで相手を確かめる）。
    """
    text = (
        "hostname r-tftp\n"
        "tftp-server flash:c2960-lanbasek9-mz.bin\n"
        "tftp-server flash version-10.3 22\n"
        "tftp-server rom alias gs3-k.101 5\n"
        "tftp-server flash:startup.cfg alias boot.cfg\n"
    )
    findings = configcheck.check(db_session, text).findings
    hits = [f for f in findings if f.title == "TFTP で誰でもファイルを取り出せます"]
    assert len(hits) == 1
    assert hits[0].evidence == [
        "tftp-server flash:c2960-lanbasek9-mz.bin",
        "tftp-server flash:startup.cfg alias boot.cfg",
    ]
    assert "no tftp-server" in hits[0].why


@pytest.mark.parametrize(
    ("line", "title", "shown", "hidden"),
    [
        # IPv4 の行だけなら、IPv6 の閉じ方は出さない
        ("telnet server enable", "telnet で入れる状態です", "undo telnet server enable", "ipv6"),
        ("http server enable", "管理用の web が開いています", "undo http server enable", "ipv6"),
        ("ftp server enable", "FTP サーバーが開いています", "undo ftp server", "ipv6"),
        # IPv6 の行だけなら、IPv4 の閉じ方は出さない
        (
            "telnet ipv6 server enable",
            "telnet で入れる状態です",
            "undo telnet ipv6 server enable",
            "undo telnet server enable",
        ),
        (
            "http ipv6 server enable",
            "管理用の web が開いています",
            "undo http ipv6 server enable",
            "undo http server enable",
        ),
        (
            "ftp ipv6 server enable",
            "FTP サーバーが開いています",
            "undo ftp ipv6 server",
            "undo ftp server、",
        ),
    ],
)
def test_huawei_fix_names_only_the_address_family_that_is_open(
    db_session, line, title, shown, hidden
):
    """Huawei の telnet・http・ftp は、開いている側（IPv4 / IPv6）の閉じ方だけを出す。

    出典: Huawei「WLAN AC V200R019C00 Command Reference」の telnet server enable、
    「智简园区网络解决方案 V100R020C10」の LAN网络站点配置（http [ ipv6 ] server enable）、
    「CloudEngine S3700, S5700 and S6700 V600R024C10 Command Reference」の
    ftp [ ipv6 ] server enable。
    IPv4 と IPv6 の口は別々に開け閉めする。
    """
    findings = configcheck.check(db_session, _VRP_HEAD + line + "\n#\n").findings
    finding = next(f for f in findings if f.title == title)
    assert shown in finding.why
    assert hidden not in finding.why


def test_huawei_fix_names_both_families_when_both_are_open(db_session):
    """IPv4 と IPv6 の両方が開いていれば、両方の閉じ方を出す。"""
    text = _VRP_HEAD + "telnet server enable\ntelnet ipv6 server enable\n#\n"
    findings = configcheck.check(db_session, text).findings
    finding = next(f for f in findings if f.title == "telnet で入れる状態です")
    assert "undo telnet server enable" in finding.why
    assert "undo telnet ipv6 server enable" in finding.why
