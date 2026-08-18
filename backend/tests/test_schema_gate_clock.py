"""表の更新は、地域の時計が読めなくても止めない。

控えのフォルダ名に日時を使っているだけなので、時計が読めないことを理由に
更新そのものを止める理由が無い。Windows で tzdata の入っていない一式を配って
しまい、ここで例外になって表の更新ができなくなったことがある。
"""

from datetime import UTC, datetime

from app.services import localtime, schema_gate


def test_the_update_still_runs_without_a_time_zone_database(monkeypatch):
    def missing(_name):
        raise KeyError("No time zone found")

    monkeypatch.setattr(localtime, "ZoneInfo", missing)
    monkeypatch.setattr(localtime, "_warned", False)

    stamp = schema_gate._now()
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is UTC
    assert stamp.strftime("%Y%m%d-%H%M%S")
