"""機器の版が読めること。

版が読めないと、その機器は弱点の照合から丸ごと漏れる。台帳に載っていても
「該当なし」として静かに扱われるので、抜けたことにも気づけない。
現場で貼られる形（show 付き・設定だけ・切り詰められたもの）を並べて押さえる。
"""

import pytest

from app.services import configparse

CASES = [
    (
        "Cisco IOS",
        "hostname sw1\nversion 15.2\n!\nCisco IOS Software, C2960X Software "
        "(C2960X-UNIVERSALK9-M), Version 15.2(7)E6, RELEASE SOFTWARE (fc3)\n",
        "15.2(7)E6",
    ),
    (
        "Cisco IOS-XE",
        "hostname sw1\nversion 17.9\n!\nCisco IOS XE Software, Version 17.09.04a\n",
        "17.09.04a",
    ),
    ("NX-OS", "hostname nx1\nversion 9.3(10)\nfeature vpc\n", "9.3(10)"),
    ("ASA", ": Saved\nASA Version 9.16(4)\nhostname fw\n", "9.16(4)"),
    ("Junos（set 形式）", "set system host-name r1\nset version 21.4R3-S5.4\n", "21.4R3-S5.4"),
    ("Junos（階層形式）", "system {\n host-name r1;\n}\nversion 22.4R3;\n", "22.4R3"),
    ("Arista", "! device: sw (DCS-7050SX3-48YC8, EOS-4.29.2F)\nhostname sw\n", "4.29.2F"),
    ("Aruba CX", "!Version ArubaOS-CX FL.10.11.1010\nhostname sw\n", "FL.10.11.1010"),
    (
        "Comware（設定だけ）",
        "sysname qx1\n#\n version 7.1.070, Release 3506P05\n#\ninterface Vlan-interface10\n",
        "7.1.070",
    ),
    ("ヤマハ", "# RTX1210 Rev.14.01.41 (Thu Nov 30 2023)\nconsole prompt rtx1\n", "14.01.41"),
    (
        "FortiGate",
        "#config-version=FGT100F-7.2.8-FW-build1639-230929:opmode=0\n"
        "config system global\n set hostname fw1\nend\n",
        "7.2.8",
    ),
    (
        "PAN-OS（書き出した XML）",
        '<config version="10.2.4"><devices><entry name="localhost.localdomain">'
        "<deviceconfig><system><hostname>pan1</hostname></system></deviceconfig>"
        "</entry></devices></config>",
        "10.2.4",
    ),
]


@pytest.mark.parametrize(("name", "text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_the_version_is_read(name: str, text: str, expected: str):
    assert configparse.parse(text).os_version == expected, name


def test_a_config_without_a_version_says_so_instead_of_guessing():
    assert configparse.parse("hostname sw1\ninterface Vlan10\n").os_version is None


SERIALS = [
    ("Cisco IOS", "hostname sw1\nSystem serial number: FOC2401A001\n", "FOC2401A001"),
    (
        "Cisco NX-OS",
        "hostname nx1\nversion 9.3(10)\n  Processor Board ID FDO24010ABC\n",
        "FDO24010ABC",
    ),
    (
        "FortiGate",
        "#config-version=FGT100F-7.2.8-FW-build1639-230929:opmode=0\n"
        "config system global\n set hostname fw1\nend\nSerial-Number: FGT100FTK12345678\n",
        "FGT100FTK12345678",
    ),
    (
        "ASA",
        ": Saved\nASA Version 9.16(4)\nhostname fw\nSerial Number: JMX1234ABCD\n",
        "JMX1234ABCD",
    ),
    ("ヤマハ", "# RTX1210 Rev.14.01.41\n# Serial=S12345678\nconsole prompt rtx1\n", "S12345678"),
]


@pytest.mark.parametrize(("name", "text", "expected"), SERIALS, ids=[case[0] for case in SERIALS])
def test_the_serial_number_is_read(name: str, text: str, expected: str):
    """保守契約や交換の照合に使う。空だと台帳の突き合わせができない。"""
    assert configparse.parse(text).serial_number == expected, name


def test_show_version_alone_is_recognised_as_cisco() -> None:
    """show version だけを貼られたときも、ベンダーを取り違えない。

    ベンダーが不明のままだと、型番が読めても照合先が決まらない。
    """
    isr = "\n".join(
        [
            "Cisco IOS XE Software, Version 16.09.08",
            "cisco ISR4331/K9 (1RU) processor with 1795979K/6147K bytes of memory.",
        ]
    )
    parsed = configparse.parse(isr)
    assert parsed.vendor is configparse.Vendor.CISCO_IOS
    assert parsed.model == "ISR4331/K9"
    assert parsed.os_version == "16.09.08"


def test_the_nexus_chassis_line_gives_the_model() -> None:
    """Nexus の show version は括弧が無い。型番が空だと EoL も弱点も引けない。"""
    text = "\n".join(
        [
            "Cisco Nexus Operating System (NX-OS) Software",
            "  system:    version 9.3(10)",
            "  cisco Nexus9000 C93180YC-EX Chassis",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is configparse.Vendor.CISCO_NXOS
    assert parsed.model == "C93180YC-EX"
    assert parsed.os_version == "9.3(10)"
