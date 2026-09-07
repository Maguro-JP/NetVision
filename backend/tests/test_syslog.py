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
    comware = "Sep  7 07:02:00 qx-01 %%10PORTSEC/4/PORTSEC_VIOLATION: Intrusion on GE1/0/5."
    titles = [item.title for item in syslog.review(None, comware).findings]
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
            "Sep  7 08:00:00 qx-01 %%10MAC/4/MAC_MOVE: MAC moved.",
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
