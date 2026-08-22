"""Config の中の秘密を、見せるときだけ伏せる。

保存は原文のまま（パーサを直したときに読み直せるように）。画面と書き出しで
そのまま出すと、画面共有やファイル受け渡しのたびに秘密が一緒に出ていく。
"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import secrets
from tests.conftest import API

CONFIG = """hostname sw-01
enable secret 5 $1$abc$xyz
enable password cisco123
username admin privilege 15 secret 5 $1$def$uvw
snmp-server community public RO
radius-server host 10.0.0.1 key MyRadiusKey
crypto isakmp key SharedThing address 203.0.113.1
service password-encryption
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
"""


def test_the_value_is_hidden_but_the_line_stays():
    """行ごと消すと、その設定が無いのか伏せてあるのか分からなくなる。"""
    hidden = secrets.mask(CONFIG)
    assert "enable secret 5 <伏せています>" in hidden.text
    assert "$1$abc$xyz" not in hidden.text
    assert "enable secret" in hidden.text


def test_what_comes_after_the_secret_is_kept():
    """RO / RW や宛先は、伏せる中身ではない。消すと意味が変わる。"""
    hidden = secrets.mask(CONFIG)
    assert "snmp-server community <伏せています> RO" in hidden.text
    assert "crypto isakmp key <伏せています> address 203.0.113.1" in hidden.text


def test_a_line_with_no_secret_value_is_left_alone():
    hidden = secrets.mask(CONFIG)
    assert "service password-encryption" in hidden.text
    assert "ip address 10.10.10.1 255.255.255.0" in hidden.text


def test_it_says_what_it_hid():
    """伏せたことを黙っていると、無いものと読まれる。"""
    hidden = secrets.mask(CONFIG)
    note = secrets.describe(hidden.counts)
    assert "パスワード" in note
    assert "SNMP のコミュニティ名" in note
    assert hidden.total == 6


def test_nothing_to_hide_says_nothing():
    hidden = secrets.mask("hostname sw-01\n!\n")
    assert hidden.counts == {}
    assert secrets.describe(hidden.counts) == ""


def _snapshot(db) -> ConfigSnapshot:
    device = Device(hostname="sw-01", vendor="Cisco", model="C9300")
    db.add(device)
    db.flush()
    snapshot = ConfigSnapshot(
        device_id=device.id,
        hostname="sw-01",
        filename="sw-01.txt",
        content=CONFIG,
        sha256="a" * 64,
    )
    db.add(snapshot)
    db.commit()
    return snapshot


def test_the_stored_config_is_still_the_original(client, db_session):
    """読み直せなくなると、パーサを直しても直した意味が無くなる。"""
    snapshot = _snapshot(db_session)
    client.get(f"{API}/configs/{snapshot.id}")
    db_session.refresh(snapshot)
    assert "$1$abc$xyz" in snapshot.content


def test_the_screen_gets_the_hidden_version(client, db_session):
    snapshot = _snapshot(db_session)
    body = client.get(f"{API}/configs/{snapshot.id}").json()
    assert "$1$abc$xyz" not in body["content"]
    assert "<伏せています>" in body["content"]
    assert body["masked_note"]


def test_the_original_can_still_be_asked_for(client, db_session):
    """伏せたままでは困る作業もある。出せるが、意識して出す形にする。"""
    snapshot = _snapshot(db_session)
    body = client.get(f"{API}/configs/{snapshot.id}", params={"reveal": True}).json()
    assert "$1$abc$xyz" in body["content"]
    assert body["masked_note"] == ""


def test_the_diff_hides_them_too(client, db_session):
    """差分では、変わった行として目立つ位置に出る。"""
    snapshot = _snapshot(db_session)
    newer = ConfigSnapshot(
        device_id=snapshot.device_id,
        hostname="sw-01",
        filename="sw-01-2.txt",
        content=CONFIG.replace("cisco123", "newpassword"),
        sha256="b" * 64,
    )
    db_session.add(newer)
    db_session.commit()

    body = client.get(f"{API}/configs/{newer.id}/diff").json()
    assert body["changed"] is True
    assert "newpassword" not in body["diff"]
    assert "cisco123" not in body["diff"]
    assert body["masked_note"]


def test_the_paste_prompt_never_carries_a_secret(db_session):
    """貼る先は社外の AI かもしれない。材料に混ぜてはいけない。"""
    from app.services import askback

    result = askback.ask(db_session, "この設定は安全ですか", context=CONFIG)
    assert result.prompt is not None
    assert "$1$abc$xyz" not in result.prompt
    assert "MyRadiusKey" not in result.prompt
    assert "伏せています" in result.prompt
