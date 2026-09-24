"""渡された Config を、台帳に入れずにその場で見る。

「これ、どこか変じゃないか」を先に知りたい場面のほうが多い。試験用に
書いたもの、業者から届いたもの、自分で書いてみたもの。
"""

import io

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import configcheck, configparse
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
