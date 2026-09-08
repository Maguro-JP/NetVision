"""Cisco の型番を、一覧ではなく命名規則で読むこと。

型番は EoL・脆弱性・保守契約を引く鍵。現場で一番多い機種が何にも
引っかからない、というのがこれまで起きていた。
"""

import pytest

from app.services import configparse, versions
from app.services.configparse import ciscomodel

#: 実機が印字する型番と、それをほどいた結果（飾りを外したもの・系列）
CASES = [
    ("WS-C2960X-48FPD-L", "C2960X-48FPD", "C2960X"),
    ("WS-C2960-24TT-L", "C2960-24TT", "C2960"),
    ("WS-C3750X-48PF-S", "C3750X-48PF", "C3750X"),
    ("WS-C3850-48P-E", "C3850-48P", "C3850"),
    ("C9200L-48P-4X-A", "C9200L-48P-4X", "C9200L"),
    ("C9300-48P-A", "C9300-48P", "C9300"),
    ("C9300-48P-E", "C9300-48P", "C9300"),
    ("C9300X-48HX-A", "C9300X-48HX", "C9300X"),
    ("C9400-SUP-1", "C9400-SUP-1", "C9400"),
    ("C9500-40X", "C9500-40X", "C9500"),
    ("C9500-48Y4C-A", "C9500-48Y4C", "C9500"),
    ("C9606R", "C9606R", "C9606R"),
    ("C1000-24T-4G-L", "C1000-24T-4G", "C1000"),
    ("C1111-8P", "C1111-8P", "C1111"),
    ("C1117-4PLTEEA", "C1117-4PLTEEA", "C1117"),
    ("ISR4331/K9", "ISR4331", "ISR4331"),
    ("ISR4451-X/K9", "ISR4451-X", "ISR4451"),
    ("ASR1001-X", "ASR1001-X", "ASR1001"),
    ("ASR1002-HX", "ASR1002-HX", "ASR1002"),
    ("CISCO2911/K9", "2911", "2911"),
    ("IE-3300-8T2S", "IE-3300-8T2S", "IE-3300"),
    ("IE-4000-8GT4G-E", "IE-4000-8GT4G", "IE-4000"),
    ("N9K-C93180YC-EX", "C93180YC-EX", "C93180YC"),
    ("C93180YC-EX", "C93180YC-EX", "C93180YC"),
    ("N9K-C9336C-FX2", "C9336C-FX2", "C9336C"),
    ("N5K-C5548UP", "C5548UP", "C5548UP"),
    ("N7K-C7010", "C7010", "C7010"),
    ("N3K-C3548P-10GX", "C3548P-10GX", "C3548P"),
    ("ASA5516-X", "ASA5516-X", "ASA5516"),
    ("FPR2110-NGFW-K9", "FPR2110-NGFW", "FPR2110"),
    ("C9800-40-K9", "C9800-40", "C9800"),
    ("C9800-L-C-K9", "C9800-L-C", "C9800-L"),
    ("AIR-AP2802I-Q-K9", "AP2802I-Q", "AP2802I"),
    ("C9120AXI-Q", "C9120AXI-Q", "C9120AXI"),
    ("SG350-28", "SG350-28", "SG350"),
    ("SG550X-48P-K9", "SG550X-48P", "SG550X"),
    ("CBS350-24P-4G", "CBS350-24P-4G", "CBS350"),
    ("WS-C2960X-48FPD-L=", "C2960X-48FPD", "C2960X"),
]


@pytest.mark.parametrize(("pid", "canonical", "family"), CASES)
def test_the_naming_rule_is_unfolded(pid: str, canonical: str, family: str) -> None:
    """一覧に無い型番でも、規則をほどけば系列と飾り無しの名前が出る。"""
    model = ciscomodel.read(pid)
    assert model is not None, pid
    assert model.canonical == canonical
    assert model.family == family


def test_the_reference_pattern_hits_the_printed_model() -> None:
    """参照データは飾り無しで書かれることが多い。印字のままの型番に当てる。"""
    assert versions.matches_model("C2960X*", "WS-C2960X-48FPD-L")
    assert versions.matches_model("ISR4331", "ISR4331/K9")
    assert versions.matches_model("C9300-48P", "C9300-48P-A")
    assert versions.matches_model("C93180YC-EX", "N9K-C93180YC-EX")
    assert versions.matches_model("C9500*", "C9500-40X")


def test_a_different_model_does_not_hit() -> None:
    """飾りを外しても、別の機種は別の機種。"""
    assert not versions.matches_model("C2960X*", "WS-C2960S-48FPD-L")
    assert not versions.matches_model("C9300-48P", "C9300-24P-A")
    assert not versions.matches_model("ISR4331", "ISR4351/K9")


def test_module_and_optic_pids_are_not_chassis() -> None:
    """DESCR の文や SFP の型番を、型番として採らない。"""
    optics = ("GLC-SX-MMD", "SFP-10G-SR", "C9300-NM-8X", "PWR-C1-715WAC", "NIM-4G-LTE-GA")
    for pid in optics:
        assert not ciscomodel.is_chassis_pid(pid), pid
    assert ciscomodel.read("Catalyst 9300 Switch") is None


INVENTORY_WITH_OPTICS_FIRST = """acc-sw-01#show inventory
NAME: "TenGigabitEthernet1/1/1", DESCR: "SFP-10GBase-SR"
PID: SFP-10G-SR          , VID: V03  , SN: AGM1234ABCD

NAME: "1", DESCR: "WS-C2960X-48FPD-L"
PID: WS-C2960X-48FPD-L   , VID: V05  , SN: FOC2000X0AB

NAME: "Switch 1 - Power Supply 1", DESCR: "PWR-C2-640WAC"
PID: PWR-C2-640WAC       , VID: V02  , SN: DTN2000X0CD
"""


def test_the_chassis_is_chosen_even_when_an_optic_comes_first() -> None:
    """最初の PID を採ると、SFP の型番がシャーシの型番になる。"""
    parsed = configparse.parse(INVENTORY_WITH_OPTICS_FIRST)
    assert parsed.model == "WS-C2960X-48FPD-L"
    assert parsed.serial_number == "FOC2000X0AB"


STACK_SHOW_VERSION = """acc-sw-02#show version
Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E7, RELEASE SOFTWARE

Switch Ports Model                     SW Version            SW Image
------ ----- -----                     ----------            ----------
*    1 52    WS-C2960X-48FPD-L         15.2(7)E7             C2960X-UNIVERSALK9-M
     2 26    WS-C2960X-24PS-L          15.2(7)E7             C2960X-UNIVERSALK9-M
"""


def test_the_stack_table_gives_the_model_when_nothing_else_does() -> None:
    """混在スタックが分かる唯一の場所。1台目を代表にする。"""
    parsed = configparse.parse(STACK_SHOW_VERSION)
    assert parsed.model == "WS-C2960X-48FPD-L"
    assert parsed.os_version == "15.2(7)E7"


def test_the_nexus_7000_chassis_line_with_a_slot_count_is_read() -> None:
    text = (
        "Cisco Nexus Operating System (NX-OS) Software\n"
        "  system:    version 8.4(4)\n"
        '  cisco Nexus7000 C7010 (10 Slot) Chassis ("Supervisor Module-2")\n'
    )
    parsed = configparse.parse(text)
    assert parsed.model == "C7010"


def test_descr_text_is_never_taken_as_a_model() -> None:
    """`cisco Catalyst 9300 Switch (X86)` のような文は型番ではない。"""
    text = "cisco Catalyst 9300 Switch (X86) processor with 1331521K bytes of memory.\n"
    assert configparse.parse(text).model is None
