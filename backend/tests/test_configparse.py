from pathlib import Path

import pytest

from app.services.configparse import Vendor, detect_vendor, expand, normalize_hostname, parse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# インターフェース名の正規化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Gi0/1", "GigabitEthernet0/1"),
        ("Gig 0/1", "GigabitEthernet0/1"),
        ("GigabitEthernet1/0/24", "GigabitEthernet1/0/24"),
        ("Te1/1/1", "TenGigabitEthernet1/1/1"),
        ("Fas 0/1", "FastEthernet0/1"),
        ("Po1", "Port-channel1"),
        ("Vl10", "Vlan10"),
        ("ge-0/0/0", "ge-0/0/0"),
        ("port1", "port1"),
    ],
)
def test_interface_names_are_normalized(raw, expected):
    assert expand(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sw2.example.com", "sw2"),
        ("acc-sw-01(FOC1234ABCD)", "acc-sw-01"),
        ("CORE-SW-01", "core-sw-01"),
    ],
)
def test_hostnames_are_normalized_for_matching(raw, expected):
    assert normalize_hostname(raw) == expected


# ---------------------------------------------------------------------------
# ベンダー判別
# ---------------------------------------------------------------------------


def test_vendor_detection():
    assert detect_vendor(load("core-sw-01")) is Vendor.CISCO_IOS
    assert detect_vendor(load("edge-rt-01")) is Vendor.JUNIPER
    assert detect_vendor(load("fw-01")) is Vendor.FORTINET
    assert detect_vendor("これはただのメモです") is Vendor.UNKNOWN


# ---------------------------------------------------------------------------
# Cisco
# ---------------------------------------------------------------------------


def test_cisco_identity_and_inventory():
    parsed = parse(load("core-sw-01"))

    assert parsed.hostname == "core-sw-01"
    assert parsed.os_name == "IOS-XE"
    assert parsed.os_version == "17.09.04a"
    assert parsed.model == "C9500-40X"
    assert parsed.serial_number == "FOC2401A001"


def test_cisco_interfaces_and_vlans():
    parsed = parse(load("core-sw-01"))
    interfaces = {i.name: i for i in parsed.interfaces}

    assert interfaces["GigabitEthernet1/0/1"].description == "->edge-rt-01"
    assert interfaces["GigabitEthernet1/0/1"].mode == "trunk"

    unused = interfaces["GigabitEthernet1/0/24"]
    assert unused.vlan == 20
    assert unused.enabled is False  # shutdown を拾う

    assert interfaces["Vlan10"].ip_address == "10.0.0.11/24"  # サブネットマスクを長さに直す
    assert parsed.vlans == {10: "SERVER", 20: "OFFICE"}


def test_cisco_cdp_detail_neighbors():
    parsed = parse(load("core-sw-01"))
    neighbors = {n.remote_key: n for n in parsed.neighbors}

    assert set(neighbors) == {"edge-rt-01", "acc-sw-osaka-01"}

    juniper = neighbors["edge-rt-01"]
    assert juniper.local_interface == "GigabitEthernet1/0/1"
    assert juniper.remote_interface == "ge-0/0/0"
    assert juniper.remote_platform == "Juniper MX204"
    # FQDN やシリアル付きでも機器名として突き合わせられる
    assert neighbors["acc-sw-osaka-01"].remote_interface == "GigabitEthernet0/24"


def test_cisco_cdp_table_neighbors():
    """`show cdp neighbors`（表形式）でも、略記を正式名に直して読む。"""
    parsed = parse(load("acc-sw-osaka-01"))
    neighbors = {n.remote_key: n for n in parsed.neighbors}

    core = neighbors["core-sw-01"]
    assert core.local_interface == "GigabitEthernet0/24"
    assert core.remote_interface == "GigabitEthernet1/0/2"
    assert "unmanaged-hub" in neighbors


# ---------------------------------------------------------------------------
# Juniper
# ---------------------------------------------------------------------------


def test_juniper_config_and_lldp():
    parsed = parse(load("edge-rt-01"))

    assert parsed.vendor is Vendor.JUNIPER
    assert parsed.hostname == "edge-rt-01"
    assert parsed.os_name == "Junos"
    assert parsed.os_version == "21.4R3-S5.4"
    assert parsed.model == "mx204"
    assert parsed.serial_number is None  # LLDP 表の "Chassis Id" を誤検出しない

    interfaces = {i.name: i for i in parsed.interfaces}
    assert interfaces["ge-0/0/0"].ip_address == "10.0.0.1/24"
    assert interfaces["ge-0/0/0"].description == "->core-sw-01"
    assert interfaces["ge-0/0/2"].enabled is False

    assert len(parsed.neighbors) == 1
    assert parsed.neighbors[0].remote_key == "core-sw-01"
    assert parsed.neighbors[0].local_interface == "ge-0/0/0"


# ---------------------------------------------------------------------------
# Fortinet
# ---------------------------------------------------------------------------


def test_fortinet_config():
    parsed = parse(load("fw-01"))

    assert parsed.vendor is Vendor.FORTINET
    assert parsed.hostname == "fw-01"
    assert parsed.os_name == "FortiOS"
    assert parsed.os_version == "7.2.8"
    assert parsed.model == "FGT100F"

    interfaces = {i.name: i for i in parsed.interfaces}
    assert interfaces["port1"].ip_address == "10.1.0.1/24"
    assert interfaces["port1"].description == "LAN"
    assert interfaces["port2"].ip_address == "203.0.113.2/30"
    assert interfaces["port8"].enabled is False


# ---------------------------------------------------------------------------
# 頑健性
# ---------------------------------------------------------------------------


def test_empty_and_garbage_input_does_not_raise():
    for text in ("", "   ", "全く関係ないテキスト\n1行目\n2行目"):
        parsed = parse(text)
        assert parsed.interfaces == []
        assert parsed.neighbors == []


def test_partial_config_returns_what_it_can():
    """ホスト名しか無いファイルでも、読めた分は返す。"""
    parsed = parse("hostname sw-partial\n")
    assert parsed.hostname == "sw-partial"
    assert parsed.interfaces == []


def test_duplicate_interface_blocks_are_merged():
    text = (
        "hostname sw-1\n"
        "interface GigabitEthernet0/1\n"
        " description first\n"
        "!\n"
        "interface Gi0/1\n"
        " switchport access vlan 30\n"
        "!\n"
    )
    parsed = parse(text)

    assert len(parsed.interfaces) == 1
    assert parsed.interfaces[0].description == "first"
    assert parsed.interfaces[0].vlan == 30


def test_cdp_and_lldp_for_the_same_link_are_merged():
    text = (
        "hostname sw-1\n"
        "sw-1#show cdp neighbors detail\n"
        "-------------------------\n"
        "Device ID: sw-2\n"
        "Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/2\n"
        "\n"
        "sw-1#show lldp neighbors detail\n"
        "-------------------------\n"
        "Local Intf: Gi0/1\n"
        "Port id: Gi0/2\n"
        "System Name: sw-2\n"
    )
    parsed = parse(text)

    assert len(parsed.neighbors) == 1
