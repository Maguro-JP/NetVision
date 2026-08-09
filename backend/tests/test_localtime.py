"""時刻の扱い。

DB には UTC で持ち、見せるときだけ使う人の時刻に直す。ここを混ぜると、
**それらしい時刻が表示されるのに9時間ずれている**という気づきにくい壊れ方をする。
"""

from datetime import UTC, datetime

from app.services import localtime
from tests.conftest import API


def test_naive_values_are_treated_as_utc():
    """タイムゾーンの付かない値を現地時刻と取り違えないこと。

    SQLite の CURRENT_TIMESTAMP は素の datetime を返す。そのまま扱うと、
    日本では9時間ずれた時刻が「それらしく」表示される。
    """
    stored = datetime(2026, 8, 9, 13, 37, 30)  # UTC で保存された値
    assert localtime.format(stored) == "2026-08-09 22:37"


def test_aware_values_are_converted_not_relabelled():
    aware = datetime(2026, 8, 9, 13, 37, 30, tzinfo=UTC)
    assert localtime.format(aware) == localtime.format(aware.replace(tzinfo=None))


def test_zone_name_can_be_shown():
    """書き出した資料が別の場所で読まれても、どの時刻か分かるように。"""
    stored = datetime(2026, 8, 9, 13, 37, 30)
    assert localtime.format(stored, with_zone=True).endswith("JST")


def test_empty_value():
    assert localtime.format(None) == ""


def test_api_returns_timestamps_with_a_timezone(client):
    """API がタイムゾーン付きで返すこと。

    付いていないと、ブラウザが**現地時刻と解釈して**ずれる。
    見た目が壊れないので、気づかないまま使われる。
    """
    client.post(
        f"{API}/devices",
        json={"hostname": "sw-01", "vendor": "Cisco", "model": "C9300", "device_type": "switch"},
    )
    logs = client.get(f"{API}/changes").json()["items"]

    assert logs, "変更履歴が記録されていない"
    created = logs[0]["created_at"]
    assert created.endswith("+00:00") or created.endswith("Z"), (
        f"タイムゾーンが付いていません: {created}"
    )


def test_exported_diagram_carries_the_local_time(client):
    """書き出した図の「◯◯時点」が、使う人の時刻であること。

    サーバの時計が UTC のまま資料に載ると、日本で見て9時間ずれる。
    """
    client.post(
        f"{API}/devices",
        json={"hostname": "sw-01", "vendor": "Cisco", "model": "C9300", "device_type": "switch"},
    )
    svg = client.get(f"{API}/topology/export.svg").text
    assert "JST" in svg


def test_broken_setting_falls_back_but_says_so(monkeypatch, caplog):
    """設定が読めないときに、黙って UTC に戻らないこと。

    UTC 表示のまま気づかれないのが一番困る。画面が出なくなるのは避けつつ、
    ログには残す。Windows で tzdata が入っていない場合もここに来る。
    """
    from app.config import settings
    from app.services import localtime as module

    monkeypatch.setattr(settings, "display_timezone", "Nowhere/Nothing")
    monkeypatch.setattr(module, "_warned", False)

    with caplog.at_level("WARNING"):
        text = module.format(datetime(2026, 8, 9, 13, 37, 30))

    assert text == "2026-08-09 13:37"  # UTC のまま出る
    assert "Nowhere/Nothing" in caplog.text


def test_tzdata_is_available():
    """Windows には zoneinfo のデータベースが無い。

    tzdata を同梱していないと、配布したバンドルで黙って UTC 表示に戻る。
    """
    from zoneinfo import ZoneInfo

    assert ZoneInfo("Asia/Tokyo") is not None
