"""LLDP の表を、見出しの並びから読めること。

列の位置を決め打ちにすると、桁がずれた出力で黙って別の値を拾う。見出しから
決めることと、見出しが無いものは読まないことの両方を見る。線が引けないことと、
線が無いことを混ぜないため。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse import lldptable
from app.services.configparse.base import ParsedConfig, Vendor


def _read(text: str) -> list[tuple[str, str, str | None]]:
    parsed = ParsedConfig(vendor=Vendor.UNKNOWN)
    lldptable.parse(text, parsed)
    return [
        (item.local_interface, item.remote_hostname, item.remote_interface)
        for item in parsed.neighbors
    ]


def test_a_column_that_contains_a_space_does_not_shift_the_rest() -> None:
    # `Port Description` に空白が入る。空白で割ると1列ずれて別の値を拾う
    text = "\n".join(
        [
            "Lcl Port  Chassis ID      Port ID       Port Description   System Name",
            "1/1/1     0000.1111.2222  Gi1/0/1       to the core        core-sw-01",
        ]
    )
    assert _read(text) == [("1/1/1", "core-sw-01", "GigabitEthernet1/0/1")]


def test_the_order_of_the_columns_comes_from_the_heading() -> None:
    text = "\n".join(
        [
            "Local Intf         Neighbor Dev        Neighbor Intf       Exptime",
            "10GE1/0/1          core-sw-01          GigabitEthernet1/0/1  95",
        ]
    )
    assert _read(text) == [("10GE1/0/1", "core-sw-01", "GigabitEthernet1/0/1")]


def test_a_table_without_a_heading_is_not_guessed() -> None:
    text = "1/1/1     0000.1111.2222  Gi1/0/1       core-sw-01"
    assert _read(text) == []


def test_the_added_vendors_draw_their_links() -> None:
    huawei = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "Local Intf         Neighbor Dev        Neighbor Intf       Exptime",
            "10GE1/0/1          core-sw-01          GigabitEthernet1/0/1  95",
        ]
    )
    fastiron = "\n".join(
        [
            "  HW: Stackable ICX7150-48P",
            "hostname icx1",
            "Lcl Port  Chassis ID      Port ID       Port Description   System Name",
            "1/1/1     0000.1111.2222  Gi1/0/1       to icx             core-sw-01",
        ]
    )
    alaxala = "\n".join(
        [
            "Model AX3650S-24T4XW",
            'hostname "ax01"',
            "Local Port   System Name        Port ID",
            "0/1          core-sw-01         GigabitEthernet1/0/5",
        ]
    )
    for text in (huawei, fastiron, alaxala):
        parsed = configparse.parse(text)
        assert len(parsed.neighbors) == 1, parsed.vendor
        assert parsed.neighbors[0].remote_hostname == "core-sw-01", parsed.vendor


def test_it_fills_in_when_the_vendor_reader_found_nothing() -> None:
    """書式の違いで線が引けないより、拾えるものは拾う。

    ベンダーごとの読み手が1本も見つけられなかったときだけ通す。見出しが
    無ければ何も足さないので、無い線を作ることにはならない。
    """
    cisco_brief = "\n".join(
        [
            "version 17.9",
            "hostname c1",
            "Local Intf   Device ID    Port ID     Hold-time",
            "Gi1/0/1      core-sw-01   Gi1/0/24    120",
        ]
    )
    aruba = "\n".join(
        [
            "ArubaOS-CX",
            "Version : FL.10.10.1050",
            "hostname acx1",
            "LOCAL-PORT  CHASSIS-ID  PORT-ID  SYSTEM-NAME",
            "1/1/1       aa:bb:cc    1/1/24   core-sw-01",
        ]
    )
    for text in (cisco_brief, aruba):
        parsed = configparse.parse(text)
        assert [item.remote_hostname for item in parsed.neighbors] == ["core-sw-01"], parsed.vendor

    quiet = "version 17.9\nhostname c1\ninterface GigabitEthernet1/0/1\n description x\n"
    assert configparse.parse(quiet).neighbors == []
