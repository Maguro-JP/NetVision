"""H3C を、NEC QX と取り違えないこと。

NEC QX は H3C の Comware を載せた機種なので、書き方はそのまま重なる。
取り違えるとメーカーが変わり、別のメーカーの保守期限と脆弱性で判定する。
しかも黙って通る。ファーウェイ VRP を分けたのと同じ理由。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

H3C = """#
 version 7.1.070, Release 6127P02
#
 sysname h3c-sw-01
#
vlan 10
 name USERS
#
interface GigabitEthernet1/0/1
 port link-type trunk
 port trunk permit vlan 10
#
 H3C S5560X-30C-EI
#
"""

QX = """#
 version 7.1.070, Release 6127P02
#
 sysname qx-sw-01
#
 NEC UNIVERGE QX-S5530
#
vlan 10
 name USERS
#
"""


def test_h3c_is_recorded_as_h3c() -> None:
    parsed = configparse.parse(H3C)
    assert parsed.vendor is Vendor.H3C
    assert configparse.VENDOR_NAMES[parsed.vendor] == "H3C"


def test_the_model_and_the_version_are_read() -> None:
    """型番が読めないと、保守期限も脆弱性も引けない。"""
    parsed = configparse.parse(H3C)
    assert parsed.model == "S5560X-30C-EI"
    assert parsed.os_version == "7.1.070"


def test_the_contents_are_read_the_same_as_comware() -> None:
    """メーカーが違うだけで、読む中身は同じ。読み手を分けない。"""
    parsed = configparse.parse(H3C)
    assert parsed.hostname == "h3c-sw-01"
    assert parsed.vlans == {10: "USERS"}
    assert parsed.interfaces[0].trunk_vlans == [10]


def test_a_nec_config_is_still_nec() -> None:
    assert configparse.parse(QX).vendor is Vendor.NEC_QX


def test_when_both_names_appear_the_nec_side_wins() -> None:
    """OEM 元の名前が資料に残っていることがある。買った先の名前を優先する。"""
    assert configparse.parse(H3C + "\n NEC UNIVERGE QX-S5530\n").vendor is Vendor.NEC_QX


def test_a_config_without_a_name_is_not_called_h3c() -> None:
    """書き方が同じなので、名乗りが無いものを H3C だと決めない。"""
    plain = "#\n sysname sw-01\n#\nvlan 10\n name USERS\n#\n"
    assert configparse.parse(plain).vendor is Vendor.NEC_QX


def test_the_commands_to_get_there_can_be_written() -> None:
    """消し方は Comware と同じ undo。差分もそのまま出せる。"""
    from app.services.configdiff import build

    future = H3C.replace(" port trunk permit vlan 10", " port trunk permit vlan 10 20")
    diff = build(H3C, future, vendor=Vendor.H3C)
    assert diff.verified
    assert "port trunk permit vlan" in diff.script
