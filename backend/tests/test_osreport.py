"""OS の版が、どれだけ散らばっているか。

数えるだけで、上げるべきかどうかの判断はしない。上げられない事情は現場に
あって、台帳からは読めないため。
"""

from app.models import Device
from app.services import osreport
from tests.conftest import API


def _device(db, hostname: str, model: str, version: str | None, os_name: str = "IOS-XE"):
    db.add(
        Device(
            hostname=hostname,
            vendor="Cisco",
            model=model,
            os_name=os_name,
            os_version=version,
        )
    )


def test_the_newest_version_of_a_model_is_found(db_session):
    _device(db_session, "acc-01", "C9300-48P", "17.06.05")
    _device(db_session, "acc-02", "C9300-48P", "17.09.04a")
    _device(db_session, "acc-03", "C9300-48P", "17.06.05")
    db_session.commit()

    report = osreport.review(db_session)
    spread = report.models[0]
    assert spread.newest == "17.09.04a"
    assert spread.behind == 2
    assert spread.mixed is True


def test_numbers_are_compared_as_numbers(db_session):
    """17.10 は 17.9 より新しい。文字として比べると逆になる。"""
    _device(db_session, "acc-01", "C9300-48P", "17.9.4")
    _device(db_session, "acc-02", "C9300-48P", "17.10.1")
    db_session.commit()

    assert osreport.review(db_session).models[0].newest == "17.10.1"


def test_a_model_where_everyone_matches_is_not_flagged(db_session):
    _device(db_session, "acc-01", "C9200-24P", "17.09.04a")
    _device(db_session, "acc-02", "C9200-24P", "17.09.04a")
    db_session.commit()

    spread = osreport.review(db_session).models[0]
    assert spread.mixed is False
    assert spread.behind == 0


def test_devices_without_a_version_are_counted_not_guessed(db_session):
    _device(db_session, "acc-01", "C9300-48P", None)
    db_session.commit()

    report = osreport.review(db_session)
    assert report.unknown_total == 1
    assert report.models[0].newest == ""
    assert "比べられません" in report.models[0].basis


def test_the_mixed_ones_come_first(db_session):
    _device(db_session, "acc-01", "C9200-24P", "17.09.04a")
    _device(db_session, "core-01", "C9500-40X", "17.06.05")
    _device(db_session, "core-02", "C9500-40X", "17.09.04a")
    db_session.commit()

    assert osreport.review(db_session).models[0].model == "C9500-40X"


def test_an_empty_ledger_says_so(db_session):
    report = osreport.review(db_session)
    assert report.models == []
    assert any("台帳にありません" in note for note in report.notes)


def test_the_api_returns_the_spread(db_session, client):
    _device(db_session, "acc-01", "C9300-48P", "17.06.05")
    _device(db_session, "acc-02", "C9300-48P", "17.09.04a")
    db_session.commit()

    response = client.get(f"{API}/os-report")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["models"][0]["behind"] == 1
    assert body["models"][0]["basis"]
