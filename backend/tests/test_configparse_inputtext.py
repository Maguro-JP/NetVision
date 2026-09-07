"""どこを通ってきたファイルでも、同じように読めること。

Windows で保存すれば行末に印が付き、メモ帳で保存すれば先頭に見えない印が
付く。吸い出す道具によっては字下げがタブになる。中身は同じなのに読めない、
という落ち方をすると、「ホスト名が読めません」と中身の問題のように出る。
"""

from app.services import configcheck, configparse, configsplit
from app.services.configparse import inputtext
from app.services.configparse.base import Vendor

PLAIN = """hostname sw-01
!
vlan 10
 name USERS
!
interface GigabitEthernet0/1
 description users
 switchport mode access
 switchport access vlan 10
!
"""

#: メモ帳などが付ける、見えない先頭の印
BOM = "﻿"


def _read(text: str):
    parsed = configparse.parse(text)
    return parsed.vendor, parsed.hostname, [(p.name, p.vlan) for p in parsed.interfaces]


def test_a_file_saved_in_notepad_is_read_the_same() -> None:
    """先頭の見えない印で、1行目のホスト名ごと読めなくなっていた。"""
    assert _read(BOM + PLAIN) == _read(PLAIN)
    assert configparse.parse(BOM + PLAIN).vendor is Vendor.CISCO_IOS


def test_a_file_from_windows_is_read_the_same() -> None:
    assert _read(PLAIN.replace("\n", "\r\n")) == _read(PLAIN)


def test_indenting_with_tabs_is_read_the_same() -> None:
    """字下げがタブだと、その下の行がまるごと落ちていた。"""
    assert _read(PLAIN.replace(" switchport", "\tswitchport")) == _read(PLAIN)


def test_all_three_at_once() -> None:
    messy = (BOM + PLAIN.replace(" switchport", "\tswitchport")).replace("\n", "\r\n")
    assert _read(messy) == _read(PLAIN)


def test_the_check_screen_sees_the_same_thing(db_session) -> None:
    """読み手と、行を直に見る検算とで、整え方が違うとちぐはぐになる。"""
    plain = configcheck.check(db_session, PLAIN)
    messy = configcheck.check(db_session, BOM + PLAIN.replace(" switchport", "\tswitchport"))
    assert [f.title for f in messy.findings] == [f.title for f in plain.findings]


def test_devices_are_still_told_apart_in_a_windows_file() -> None:
    text = "hostname sw-a\ninterface Gi0/1\nhostname sw-b\ninterface Gi0/2\n"
    assert len(configsplit.split(text.replace("\n", "\r\n"))) == 2


def test_nothing_else_is_touched() -> None:
    """整えるのは意味を持たない差だけ。行の数も語の並びも変えない。"""
    assert inputtext.clean(PLAIN) == PLAIN
    assert len(inputtext.clean(BOM + PLAIN).splitlines()) == len(PLAIN.splitlines())
