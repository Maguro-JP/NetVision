"""ポート名の略記と種別判定。

図で読めない・取り違えるのを防ぐための処理なので、
「別のポートに見えてしまう」削り方をしていないかを確かめる。
"""

import pytest

from app.services.portlabel import Media, format, media_of, split


@pytest.mark.parametrize(
    ("name", "short", "number", "media"),
    [
        ("TenGigabitEthernet1/1/1", "Te", "1/1/1", Media.TEN),
        ("GigabitEthernet1/0/24", "Gi", "1/0/24", Media.GIGA),
        ("FastEthernet0/1", "Fa", "0/1", Media.FAST),
        ("Port-channel10", "Po", "10", Media.LAG),
        ("HundredGigE1/0/1", "Hu", "1/0/1", Media.HUNDRED),
        # NEC IX は GigaEthernet（bit が無い）。サブインターフェース番号まで含む
        ("GigaEthernet0.1", "GE", "0.1", Media.GIGA),
        # Comware の集約
        ("Bridge-Aggregation1", "BAGG", "1", Media.LAG),
        # Juniper は接頭辞そのものが種別
        ("ge-0/0/1", "ge", "0/0/1", Media.GIGA),
        ("xe-0/0/0", "xe", "0/0/0", Media.TEN),
        ("ae0", "ae", "0", Media.LAG),
        ("Vlan10", "Vlan", "10", Media.VIRTUAL),
        # 既に略記で書かれていても同じ結果になること
        ("Gi1/0/24", "Gi", "1/0/24", Media.GIGA),
        ("Te1/1/1", "Te", "1/1/1", Media.TEN),
    ],
)
def test_split(name, short, number, media):
    assert split(name) == (short, number, media)


def test_unknown_names_are_left_alone():
    """読めない名前を勝手に削ると別のポートに見える。丸ごと残す。"""
    assert format("port1", "short") == "port1"
    assert format("port1", "number") == "port1"
    assert media_of("port1") is Media.OTHER


def test_format_styles():
    name = "TenGigabitEthernet1/1/1"
    assert format(name, "full") == name
    assert format(name, "short") == "Te1/1/1"
    assert format(name, "number") == "1/1/1"


def test_number_style_keeps_the_prefix_when_the_media_is_unknown():
    """種別が色に移せていないなら、接頭辞を削ってはいけない。

    削ってよいのは「色に移した分だけ」。管理ポートのように色で表せないものは
    番号だけにすると、何のポートか分からなくなる。
    """
    assert format("mgmt0", "number") == "Mgmt0"
    assert format("", "number") == ""


def test_gigaethernet_is_not_confused_with_gigabitethernet():
    """NEC IX の GigaEthernet と Cisco の GigabitEthernet は別物として略す。

    同じ `Gi` に丸めると、図の上で機器の系統が分からなくなる。
    """
    assert format("GigaEthernet0.1", "short") == "GE0.1"
    assert format("GigabitEthernet0/1", "short") == "Gi0/1"
