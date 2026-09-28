"""機器のログを読んで、何が起きたかを出す。

Config は「どう設定されているか」で、ログは「実際に何が起きたか」。
設定は正しいのに落ちている、片系で動き続けている、は片方だけでは分からない。
"""

from app.models import Device
from app.services import syslog
from tests.conftest import API

# 実物どおりの行。折り返すと読めなくなるので、組み立てて作る
SAMPLE = "\n".join(
    [
        "<187>Aug 22 10:00:01 core-sw-01 %LINK-3-UPDOWN: "
        "Interface GigabitEthernet1/0/24, changed state to down",
        "<189>Aug 22 10:00:02 core-sw-01 %LINEPROTO-5-UPDOWN: "
        "Line protocol on Interface GigabitEthernet1/0/24, changed state to down",
        "Aug 22 10:00:31 core-sw-01 %LINK-3-UPDOWN: "
        "Interface GigabitEthernet1/0/24, changed state to up",
        "2026-08-22T10:05:00 edge-rt-01 %OSPF-5-ADJCHG: "
        "Process 1, Nbr 10.0.0.2 on Gi0/1 from FULL to DOWN",
        "Aug 22 10:06:00 edge-rt-01 %SYS-5-CONFIG_I: Configured from console by admin",
        "これはログではありません",
        "<190>Aug 22 10:07:00 qx-01 %%10IFNET/3/PHY_UPDOWN: "
        "Physical state on GigabitEthernet1/0/5 changed to down",
    ]
)


def test_a_line_is_broken_into_when_where_and_what():
    event = syslog.parse_line(
        "<187>Aug 22 10:00:01 core-sw-01 %LINK-3-UPDOWN: Interface Gi1/0/24, changed state to down"
    )
    assert event is not None
    assert event.host == "core-sw-01"
    assert event.code == "LINK-3-UPDOWN"
    assert event.severity == 3
    assert "changed state to down" in event.message
    assert event.at is not None


def test_the_device_own_severity_wins_over_the_header():
    """頭の数字は、途中の収集の仕組みで書き換わることがある。"""
    event = syslog.parse_line(
        "<134>Aug 22 10:00:01 sw-01 %LINK-3-UPDOWN: Interface Gi1/0/1, changed state to down"
    )
    assert event.severity == 3


def test_another_vendor_wording_is_read_too():
    event = syslog.parse_line(
        "<190>Aug 22 10:07:00 qx-01 %%10IFNET/3/PHY_UPDOWN: Physical state changed to down"
    )
    assert event.code == "IFNET/3/PHY_UPDOWN"
    assert event.severity == 3
    assert event.host == "qx-01"


def test_a_line_with_no_stamp_and_no_code_is_not_a_log_line():
    assert syslog.parse_line("これはログではありません") is None
    assert syslog.parse_line("   ") is None


def test_what_it_means_is_said_in_plain_words():
    """知らない語のまま出しても、読む人には何も伝わらない。"""
    assert "落ちた" in syslog.meaning_of("LINK-3-UPDOWN")
    assert "設定が変えられた" == syslog.meaning_of("SYS-5-CONFIG_I")
    assert syslog.meaning_of("SOMETHING-9-UNKNOWN") == ""


def test_it_counts_rather_than_lists(db_session):
    """1行ずつ眺めるためのものではない。何度起きたかが分かると当たりが付く。"""
    report = syslog.review(db_session, SAMPLE)
    assert report.lines == 7
    assert report.parsed == 6
    assert report.unreadable == 1

    link = next(p for p in report.patterns if p.code == "LINK-3-UPDOWN")
    assert link.count == 2
    assert link.hosts == ["core-sw-01"]
    assert link.meaning


def test_the_heavy_ones_come_first(db_session):
    report = syslog.review(db_session, SAMPLE)
    severities = [p.severity for p in report.patterns if p.severity is not None]
    assert severities == sorted(severities)


def test_the_period_and_the_devices_are_summarised(db_session):
    report = syslog.review(db_session, SAMPLE)
    assert report.first_at is not None and report.last_at is not None
    assert report.first_at < report.last_at
    top = report.by_host[0]
    assert top["host"] == "core-sw-01"
    assert top["count"] == 3
    assert top["heavy"] == 2


def test_unreadable_lines_are_reported_not_dropped(db_session):
    """落としてしまうと、読めていないことに気づけない。"""
    report = syslog.review(db_session, SAMPLE)
    assert any("読めませんでした" in note for note in report.notes)


def test_the_names_are_matched_against_the_ledger(db_session):
    db_session.add(Device(hostname="core-sw-01", vendor="Cisco", model="C9500"))
    db_session.commit()

    report = syslog.review(db_session, SAMPLE)
    assert report.known_hosts == ["core-sw-01"]
    assert "edge-rt-01" in report.unknown_hosts
    assert any("台帳に見当たらない" in note for note in report.notes)


def test_nothing_readable_says_so(db_session):
    report = syslog.review(db_session, "あいうえお\nかきくけこ\n")
    assert report.parsed == 0
    assert any("読める行がありません" in note for note in report.notes)


def test_the_api_takes_pasted_logs(client):
    res = client.post(f"{API}/syslog", json={"text": SAMPLE})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["parsed"] == 6
    assert body["patterns"][0]["severity_label"] == "エラー"
    assert body["patterns"][0]["meaning"]


def test_the_api_refuses_an_empty_paste(client):
    assert client.post(f"{API}/syslog", json={"text": "  "}).status_code == 422


def test_the_api_takes_dropped_files(client):
    res = client.post(
        f"{API}/syslog/upload",
        files=[
            ("files", ("a.log", SAMPLE.encode("utf-8"), "text/plain")),
            ("files", ("b.log", SAMPLE.encode("cp932"), "text/plain")),
        ],
    )
    assert res.status_code == 200, res.text
    # 2つのファイルは、つなげて1つとして数える
    assert res.json()["parsed"] == 12


def test_the_fortigate_level_is_read_as_the_weight() -> None:
    """FortiGate は key=value で書き、重さは `level=` に入る。

    読まないと、その機器の出来事だけ重さが分からないまま並ぶ。重い順に
    見ている人が、いちばん重いものを見落とす。
    """
    warning = syslog.parse_line(
        'Sep  7 01:01:00 fw-01 date=2026-09-07 level=warning msg="Admin login failed"'
    )
    assert warning is not None
    assert warning.severity == 4

    critical = syslog.parse_line('Sep  7 01:02:00 fw-01 level=critical msg="HA member down"')
    assert critical is not None
    assert critical.severity == 2

    # 事象名から分かるものは、これまでどおりそちらを採る
    cisco = syslog.parse_line(
        "Sep  7 01:00:01 core-sw-01 %LINK-3-UPDOWN: Interface Gi1/0/1, changed state to down"
    )
    assert cisco is not None
    assert cisco.severity == 3


def test_huawei_event_names_are_explained_too() -> None:
    """同じ出来事でも、機種によって事象名の語が違う。

    引けないと、ログの一覧に事象名だけが並ぶ。何が起きたのかを読む人が
    その場で調べることになり、貼って見るという使い方にならない。
    """
    assert syslog.meaning_of("IFNET/4/IF_STATE")
    assert syslog.meaning_of("OSPF/3/NBR_DOWN")
    assert syslog.meaning_of("SSH/5/SSH_USER_LOGIN")
    # 知らないものは、それらしい言葉を作らない
    assert syslog.meaning_of("KNOWN_TO_NOBODY/9/XYZ") == ""


def test_a_flapping_port_is_found_on_huawei_too() -> None:
    """同じ出来事でも、機種によって事象名が違う。

    1つの語だけを見ていると、そのベンダーの機器だけ「揺れていない」ことに
    なる。線や光の部品を疑う手掛かりが、その機器では出ない。
    """
    lines = []
    for index in range(8):
        state = "DOWN" if index % 2 == 0 else "UP"
        lines.append(
            f"Sep  7 03:0{index}:00 hw-01 "
            f"%%01IFNET/4/IF_STATE: Interface 10GE1/0/1 turned into {state} state."
        )
    report = syslog.review(None, "\n".join(lines))
    titles = [item.title for item in report.findings]
    assert [title for title in titles if "何度も上下しています" in title]


def test_a_power_or_heat_alarm_is_found_on_every_vendor() -> None:
    """電源と熱は、放っておくと機器ごと止まる。

    語が違うだけで見逃すと、その機器の異常だけ出ない。いちばん重い部類の
    出来事なので、機種によって黙るのは困る。
    """
    cases = [
        "Sep  7 06:00:00 hw-01 %%01DEVM/4/hwPowerRemove: Power 1 is removed.",
        "Sep  7 06:01:00 hw-01 %%01DEVM/4/hwEntityTemperature: The temperature exceeded.",
        "Sep  7 06:02:00 qx-01 %%10DEV/4/POWER_FAILED: Power 1 failed.",
        "Sep  7 06:03:00 c1 %PLATFORM_THERMAL-1-OVERTEMP: Temperature is too high",
    ]
    for line in cases:
        titles = [item.title for item in syslog.review(None, line).findings]
        assert [title for title in titles if "電源か冷却" in title], line


def test_a_port_security_violation_is_said_in_the_more_specific_way() -> None:
    """同じ行を2つの見方で出すと、どちらを見ればよいのか分からなくなる。

    「許していない端末が挿された」のほうが具体的で、次にやることも書ける。
    機器が `Intrusion` と書いていても、そちらに寄せる。
    """
    necqx = "Sep  7 07:02:00 qx-01 %%10PORTSEC/4/PORTSEC_VIOLATION: Intrusion on GE1/0/5."
    titles = [item.title for item in syslog.review(None, necqx).findings]
    assert titles == ["許していない端末が挿されています"]

    # 本当に攻撃として記録された行は、これまでどおり上げる
    attack = 'Sep  7 07:10:00 fw-01 level=alert type=ips msg="Attack detected"'
    titles = [item.title for item in syslog.review(None, attack).findings]
    assert [title for title in titles if "攻撃として記録" in title]


def test_mac_moves_and_rogue_dhcp_are_found_on_every_vendor() -> None:
    """どちらも、線がループしているか、誰かが機器を挿したかを疑うところ。

    語が違うだけで見逃すと、その機器では何も出ない。
    """
    cases = {
        "同じ機器が、複数のポートに現れています": [
            "Sep  7 08:00:00 hw-01 %%01L2IF/4/MFLP_VLAN: MAC move detected on 10GE1/0/1.",
            "Sep  7 08:00:01 c1 %SW_MATM-4-MACFLAP_NOTIF: Host is flapping between ports",
        ],
        "許していない機器が、住所を配ろうとしています": [
            "Sep  7 08:01:00 qx-01 %%10DHCPSNP/4/DHCPSNP_UNTRUSTED: Untrusted port got a reply.",
            "Sep  7 08:01:00 c1 %DHCP_SNOOPING-5-UNTRUSTED_PORT: received on untrusted port",
        ],
    }
    for expected, lines in cases.items():
        for line in lines:
            titles = [item.title for item in syslog.review(None, line).findings]
            assert expected in titles, line


def test_a_change_and_the_trouble_after_it_are_linked_on_every_vendor() -> None:
    """設定を変えた、の言い方も機種で違う。

    取りこぼすと、直後に出た重い事象と結び付かない。原因の当たりとしては
    最初に見るところなので、機種によって出ないのは困る。
    """
    vrp = [
        "Sep  7 08:00:00 hw-01 %%01CFM/4/SAVE: The configuration was saved by admin.",
        "Sep  7 08:01:00 hw-01 %%01IFNET/3/LINK_STATE: Interface 10GE1/0/1 entered DOWN.",
    ]
    titles = [item.title for item in syslog.review(None, "\n".join(vrp)).findings]
    assert [title for title in titles if "設定を変えた直後" in title]

    # 変更が無ければ、結び付けない
    alone = ["Sep  7 08:01:00 hw-01 %%01IFNET/3/LINK_STATE: Interface 10GE1/0/1 entered DOWN."]
    titles = [item.title for item in syslog.review(None, "\n".join(alone)).findings]
    assert not [title for title in titles if "設定を変えた直後" in title]


def test_a_routing_neighbour_going_down_is_found_on_every_vendor() -> None:
    """相手が落ちた、の言い方も機種で違う。

    VRP は BGP を `STATE_CHANGED`、OSPF を `NBR_DOWN` と書く。取りこぼすと、
    経路が揺れているのにその機器では何も出ない。
    """
    vrp_bgp = [
        f"Sep  7 09:0{index}:00 hw-01 %%01BGP/3/STATE_CHANGED: Peer 10.0.0.2 changed to IDLE."
        for index in range(4)
    ]
    titles = [item.title for item in syslog.review(None, "\n".join(vrp_bgp)).findings]
    assert [title for title in titles if "経路の相手が繰り返し落ちています" in title]

    # 1回だけなら、再起動でも起きる。繰り返してはじめて言う
    once = syslog.review(None, vrp_bgp[0]).findings
    assert not [item for item in once if "繰り返し落ちています" in item.title]


def test_a_native_vlan_mismatch_is_found_however_the_device_words_it() -> None:
    """NEC QX / VRP は同じ食い違いを PVID の名前で書く。

    タグ無しで渡す VLAN が両端で違うと、その VLAN の通信だけが漏れる。
    設定を見ないと分からない食い違いなので、ログから出せる意味は大きい。
    """
    lines = [
        "Sep  7 10:00:00 hw-01 %%01LLDP/4/PVID_INCONSISTENT: PVID mismatch on 10GE1/0/1.",
        "Sep  7 10:01:00 qx-01 %%10LLDP/4/LLDP_PVID_INCONSISTENT: PVID inconsistent on GE1/0/1.",
        "Sep  7 10:02:00 c1 %CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch on Gi1/0/1",
    ]
    for line in lines:
        titles = [item.title for item in syslog.review(None, line).findings]
        assert [title for title in titles if "タグ無し VLAN" in title], line


# RFC 5424 "The Syslog Protocol" 6. Syslog Message Format の形。
# 以前は PRI の後の版数（`1`）で止まり、時刻も機器名も読めていなかった


def test_an_rfc5424_line_gives_its_time_host_and_event(monkeypatch):
    from zoneinfo import ZoneInfo

    from app.services import localtime

    monkeypatch.setattr(localtime, "zone", lambda: ZoneInfo("Asia/Tokyo"))
    line = (
        '<165>1 2026-09-27T01:00:00.123Z core-sw-01 - - - [meta sequenceId="1"] '
        "%LINK-3-UPDOWN: Interface Gi1/0/1, changed state to down"
    )
    event = syslog.parse_line(line)

    assert event is not None
    assert event.host == "core-sw-01"
    # 時差の付いた時刻は、ほかの形と並ぶよう表示の時間帯に直す
    assert event.at is not None and (event.at.hour, event.at.minute) == (10, 0)
    assert event.code == "LINK-3-UPDOWN"
    assert event.message.startswith("Interface Gi1/0/1")


def test_rfc5424_nil_values_are_left_empty():
    event = syslog.parse_line("<34>1 - - - - - - disk full")
    assert event is not None
    assert event.at is None and event.host is None
    assert event.message == "disk full"


def test_a_priority_beyond_191_is_not_read_as_a_severity():
    event = syslog.parse_line("<999>Sep 27 10:00:00 sw1 msg")
    assert event is not None
    assert event.severity is None
