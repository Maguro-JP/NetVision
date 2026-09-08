"""OS の名前は、機器が印字した名前だけを入れること。

こちらの記憶で付けた名前を入れると、別のメーカーの名前を当てて黙って通る
ことがあった。印字が無ければ空のまま。無いのと読めていないのを混ぜない。
"""

from app.services import configparse
from app.services.configparse.base import Vendor


def test_alaxala_reads_the_os_name_the_device_prints() -> None:
    text = 'ALAXALA AX2530S\nS/W: OS-L2B Ver. 4.14.B\nhostname "ax-01"\n'
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.ALAXALA
    assert parsed.os_name == "OS-L2B"


def test_alaxala_without_the_line_stays_empty() -> None:
    text = 'hostname "ax-01"\n! ALAXALA\ninterface gigabitethernet 0/1\n'
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.ALAXALA
    assert parsed.os_name is None


def test_yamaha_has_no_os_name_to_read() -> None:
    """1行目は型番と Rev. だけ。こちらで名前を付けない。"""
    text = "# RTX830 Rev.15.02.28 (Tue Nov 26 18:39:03 2019)\nip lan1 address 10.0.0.1/24\n"
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.YAMAHA
    assert parsed.os_name is None
    assert parsed.os_version == "15.02.28"


def test_dell_reads_os10_only_when_the_device_prints_it() -> None:
    printed = (
        "Dell EMC Networking OS10 Enterprise\nOS10 Enterprise. Version 10.5.4.0\n"
        "hostname sw-01\ninterface ethernet 1/1/1\n"
    )
    plain = "hostname sw-01\ninterface ethernet 1/1/1\n switchport access vlan 10\n"
    assert configparse.parse(printed).os_name == "OS10"
    assert configparse.parse(plain).os_name is None


def test_vyos_and_edgeos_are_told_apart_by_what_the_device_prints() -> None:
    """2つは別の製品。混ぜた名前を入れない。小文字で印字されても同じ語。"""
    tail = "set system host-name 'rt-01'\nset interfaces ethernet eth0 address '10.0.0.1/24'\n"
    vyos = "// vyos version: 1.4\n" + tail
    edge = "/* EdgeOS 2.0.9 */\n" + tail
    assert configparse.parse(vyos).os_name == "VyOS"
    assert configparse.parse(edge).os_name == "EdgeOS"
    assert configparse.parse(tail).os_name is None
