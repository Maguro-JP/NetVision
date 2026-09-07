"""1つのファイルに何台分も入っているとき、混ぜないこと。

そのまま読むと、2台目以降の口と VLAN が1台目に付く。指摘が1つ増えるより
悪い。台帳に、その機器が持っていない口が載るからである。
"""

from app.services import configparse, configsplit
from tests.conftest import API

TWO_DEVICES = """!
version 17.6
hostname sw-a
!
vlan 10
 name USERS
!
interface GigabitEthernet0/1
 description users
 switchport mode access
 switchport access vlan 10
!
end
!
hostname sw-b
!
vlan 20
 name SERVERS
!
interface GigabitEthernet0/2
 description servers
 switchport mode access
 switchport access vlan 20
!
end
"""


def test_two_devices_in_one_file_come_apart() -> None:
    chunks = configsplit.split(TWO_DEVICES)
    assert len(chunks) == 2
    assert [configparse.parse(chunk).hostname for chunk in chunks] == ["sw-a", "sw-b"]


def test_the_ports_do_not_move_between_devices() -> None:
    """いちばん困るのは、持っていない口が台帳に載ること。"""
    first, second = configsplit.split(TWO_DEVICES)
    assert [port.name for port in configparse.parse(first).interfaces] == ["GigabitEthernet0/1"]
    assert [port.name for port in configparse.parse(second).interfaces] == ["GigabitEthernet0/2"]


def test_what_is_written_before_the_first_name_goes_to_the_first_device() -> None:
    """version などの行を捨てない。型番や版を読む手掛かりになる。"""
    first = configsplit.split(TWO_DEVICES)[0]
    assert "version 17.6" in first


def test_one_device_is_left_as_it_is() -> None:
    text = "hostname sw-a\ninterface GigabitEthernet0/1\n"
    assert configsplit.split(text) == [text]


def test_the_same_name_written_twice_is_not_split() -> None:
    """同じ機器の中で2度書かれているだけなら、分けるとかえって壊す。"""
    text = "hostname sw-a\ninterface GigabitEthernet0/1\nhostname sw-a\n"
    assert configsplit.split(text) == [text]


def test_a_name_inside_the_banner_does_not_split_anything() -> None:
    """文言の中の機器名で分けると、設定が真ん中で切れる。"""
    text = (
        "hostname sw-a\n"
        "banner motd ^C\n"
        "hostname sw-zzz と書かないこと\n"
        "^C\n"
        "interface GigabitEthernet0/1\n"
    )
    assert configsplit.split(text) == [text]


def test_devices_written_in_different_makers_words_are_split() -> None:
    """機器名の書き方はメーカーで違う。片方だけ見ると混ざったままになる。"""
    text = "sysname qx-01\nvlan 10\n#\nset system host-name mx-01\nset vlans A vlan-id 20\n"
    assert len(configsplit.split(text)) == 2


def test_the_check_screen_shows_each_device_on_its_own(client) -> None:
    response = client.post(
        f"{API}/config-check/upload",
        files=[("files", ("both.txt", TWO_DEVICES.encode("utf-8"), "text/plain"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["hostname"] for item in body] == ["sw-a", "sw-b"]
    assert [item["interfaces"] for item in body] == [1, 1]


def test_taking_it_in_registers_each_device_on_its_own(client) -> None:
    """混ぜて取り込むと、持っていない口が台帳に載る。"""
    response = client.post(
        f"{API}/configs/upload?create_missing=true",
        files=[("files", ("both.txt", TWO_DEVICES.encode("utf-8"), "text/plain"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["hostname"] for item in body] == ["sw-a", "sw-b"]
    assert [item["interface_count"] for item in body] == [1, 1]
