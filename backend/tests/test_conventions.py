"""取り込んだ Config から、その現場の流儀を覚える。

生成する Config が一般的な書き方のままだと、現場の決めごとと食い違う。
覚え方は数えるだけで、揃っていないものは覚えない。
"""

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import conventions
from tests.conftest import API


def _add(db, hostname: str, body: str, digest: str) -> Device:
    device = Device(hostname=hostname, vendor="Cisco", model="C9300")
    db.add(device)
    db.flush()
    db.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=hostname,
            filename=f"{hostname}.txt",
            content=body,
            sha256=digest,
        )
    )
    db.flush()
    return device


def _config(hostname: str, vlans: list[int], extra: str = "") -> str:
    lines = [f"hostname {hostname}", "!"]
    for vlan in vlans:
        lines += [f"vlan {vlan}", f" name SEG{vlan}", "!"]
    lines += [
        "no ip http server",
        "service password-encryption",
        "logging 10.0.0.9",
        "ntp server 10.0.0.10",
        "spanning-tree mode rapid-pvst",
        "enable secret 5 $1$abcd$xyz",
        extra,
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture()
def field_site(db_session):
    _add(db_session, "hq-sw-01", _config("hq-sw-01", [10, 20, 30]), "a" * 64)
    _add(db_session, "hq-sw-02", _config("hq-sw-02", [10, 20, 40]), "b" * 64)
    _add(db_session, "br-sw-01", _config("br-sw-01", [10, 50]), "c" * 64)
    db_session.commit()
    return db_session


def test_one_device_is_not_a_convention(db_session):
    """1台しか無いものを「この現場のやり方」とは呼べない。"""
    _add(db_session, "only-sw-01", _config("only-sw-01", [10, 20]), "a" * 64)
    db_session.commit()
    house = conventions.learn(db_session)
    assert house.devices_seen == 1
    assert house.learned == []


def test_it_learns_the_vlan_step(field_site):
    house = conventions.learn(field_site)
    assert house.vlan_step == 10
    assert any(item.topic == "VLAN 番号の刻み" for item in house.learned)


def test_it_learns_where_logs_and_time_go(field_site):
    house = conventions.learn(field_site)
    assert house.syslog_hosts == ["10.0.0.9"]
    assert house.ntp_hosts == ["10.0.0.10"]
    assert house.stp_mode == "rapid-pvst"


def test_it_learns_the_lines_every_device_has(field_site):
    house = conventions.learn(field_site)
    assert "no ip http server" in house.baseline_lines
    assert "service password-encryption" in house.baseline_lines
    assert "logging 10.0.0.9" in house.baseline_lines


def test_secrets_are_never_learned(field_site):
    """見せられないものを生成に混ぜると、人が確かめられなくなる。"""
    house = conventions.learn(field_site)
    assert not any("enable secret" in line for line in house.baseline_lines)
    # 語が出てくるだけで中身を持たない行は、写して構わない
    assert "service password-encryption" in house.baseline_lines


def test_something_only_one_device_does_is_not_learned(db_session):
    """揃っていないものを流儀と呼ぶと、片方を押し付けることになる。"""
    _add(db_session, "sw-01", _config("sw-01", [10, 20], "ip domain-name a.example"), "a" * 64)
    _add(db_session, "sw-02", _config("sw-02", [10, 20]), "b" * 64)
    _add(db_session, "sw-03", _config("sw-03", [10, 20]), "c" * 64)
    db_session.commit()
    house = conventions.learn(db_session)
    assert not any("a.example" in line for line in house.baseline_lines)


def test_only_the_newest_config_of_each_device_counts(db_session):
    """直す前と直した後が両方票を持つと、直した意味が無くなる。"""
    device = _add(db_session, "sw-01", _config("sw-01", [10, 20]), "a" * 64)
    db_session.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=device.hostname,
            filename="sw-01-new.txt",
            content=_config("sw-01", [100, 101, 102]),
            sha256="d" * 64,
        )
    )
    _add(db_session, "sw-02", _config("sw-02", [100, 101, 102]), "b" * 64)
    db_session.commit()

    house = conventions.learn(db_session)
    assert house.vlan_step == 1


def test_everything_learned_says_how_many_devices_agreed(field_site):
    """裏で勝手に学んで裏で使うのがいちばん困る。台数まで見せる。"""
    house = conventions.learn(field_site)
    assert house.learned
    for item in house.learned:
        assert item.total >= 2
        assert 0 < item.share <= 1


# ---------------------------------------------------------------------------
# 覚えた流儀を、生成に使う
# ---------------------------------------------------------------------------


def test_the_generated_config_follows_the_house_style(db_session):
    """一般的な書き方より、その現場で揃っている書き方のほうが正しい。"""
    from app.services import addition

    upstream = _add(
        db_session,
        "hq-sw-01",
        _config("hq-sw-01", [100, 101, 102]),
        "a" * 64,
    )
    _add(db_session, "hq-sw-02", _config("hq-sw-02", [100, 101, 102]), "b" * 64)
    db_session.commit()

    plan = addition.build(
        db_session,
        addition.AdditionRequest(upstream_device_id=upstream.id, hostname="hq-sw-03"),
    )
    # 1 刻みの現場なので、10 飛ばしにしない
    assert plan.vlan_id == 103
    decision = next(d for d in plan.decisions if d.topic == "VLAN 番号")
    assert "1 刻み" in decision.why
    assert any("台の Config" in line for line in decision.evidence)


def test_the_house_baseline_is_copied_into_the_new_device(db_session):
    from app.services import addition

    upstream = _add(db_session, "hq-sw-01", _config("hq-sw-01", [10, 20]), "a" * 64)
    _add(db_session, "hq-sw-02", _config("hq-sw-02", [10, 20]), "b" * 64)
    db_session.commit()

    plan = addition.build(
        db_session,
        addition.AdditionRequest(upstream_device_id=upstream.id, hostname="hq-sw-03"),
    )
    assert "logging 10.0.0.9" in plan.device_config
    assert "ntp server 10.0.0.10" in plan.device_config
    assert any("写しました" in note for note in plan.device_notes)


def test_a_line_already_there_is_not_added_twice(db_session):
    """同じ行が2回出てくる Config は、貼った人が間違えたように見える。"""
    from app.services import addition

    upstream = _add(db_session, "hq-sw-01", _config("hq-sw-01", [10, 20]), "a" * 64)
    _add(db_session, "hq-sw-02", _config("hq-sw-02", [10, 20]), "b" * 64)
    db_session.commit()

    plan = addition.build(
        db_session,
        addition.AdditionRequest(upstream_device_id=upstream.id, hostname="hq-sw-03"),
    )
    assert plan.device_config.count("no ip http server") == 1


def test_the_api_shows_what_was_learned(client, field_site):
    body = client.get(f"{API}/conventions").json()
    assert body["devices_seen"] == 3
    assert body["vlan_step"] == 10
    assert body["learned"]
    assert all(item["total"] for item in body["learned"])
