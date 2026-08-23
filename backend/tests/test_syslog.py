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
