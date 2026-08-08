"""NEC の機器（IX / QX）を読めること。

IX は自社系、QX は H3C Comware ベースで書式がまったく違う。
どちらも Cisco 風に見える箇所があるので、取り違えないことも確かめる。
"""

from pathlib import Path

from app.services import configparse
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


# ---------------------------------------------------------------------------
# IX シリーズ
# ---------------------------------------------------------------------------


def test_ix_is_detected_and_read():
    parsed = configparse.parse(load("ix-rt-01"))

    assert parsed.vendor is Vendor.NEC_IX
    assert parsed.hostname == "ix-rt-01"
    assert parsed.model == "IX2215"
    assert parsed.os_name == "IX"
    assert parsed.os_version == "10.6.21"
    assert parsed.serial_number == "2215X0012345"


def test_ix_interfaces_keep_their_sub_interface_number():
    """IX は GigaEthernet0.0 のように番号まで含めて1つのインターフェース。"""
    parsed = configparse.parse(load("ix-rt-01"))
    names = [i.name for i in parsed.interfaces]

    assert "GigaEthernet0.0" in names
    assert "Loopback0.0" in names

    wan = next(i for i in parsed.interfaces if i.name == "GigaEthernet0.0")
    assert wan.description == "WAN-回線"
    assert wan.ip_address == "203.0.113.2/30"


def test_ix_vendor_name_is_nec():
    parsed = configparse.parse(load("ix-rt-01"))
    assert configparse.VENDOR_NAMES[parsed.vendor] == "NEC"


# ---------------------------------------------------------------------------
# QX シリーズ（Comware）
# ---------------------------------------------------------------------------


def test_qx_is_detected_and_read():
    parsed = configparse.parse(load("qx-sw-01"))

    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "qx-sw-01"
    assert parsed.model == "QX-S5528P"
    assert parsed.os_name == "Comware"
    assert parsed.os_version == "5.20.99"


def test_qx_interfaces_and_vlans():
    parsed = configparse.parse(load("qx-sw-01"))

    access = next(i for i in parsed.interfaces if i.name == "GigabitEthernet1/0/8")
    assert access.mode == "access"
    assert access.vlan == 10
    assert access.description == "client-port"

    trunk = next(i for i in parsed.interfaces if i.name == "GigabitEthernet1/0/1")
    assert trunk.mode == "trunk"

    assert parsed.vlans == {10: "USERS", 20: "SERVERS"}


def test_qx_lldp_neighbors_are_read():
    """Comware の display lldp neighbor-information から結線が引けること。"""
    parsed = configparse.parse(load("qx-sw-01"))

    assert len(parsed.neighbors) == 1
    neighbor = parsed.neighbors[0]
    assert neighbor.remote_hostname == "core-sw-01"
    assert neighbor.remote_interface == "GigabitEthernet1/0/24"


# ---------------------------------------------------------------------------
# 取り違えないこと
# ---------------------------------------------------------------------------


def test_cisco_is_not_mistaken_for_nec():
    """NEC の判別を Cisco より先に走らせているので、誤判定しないことを確かめる。"""
    for name in ("core-sw-01", "acc-sw-osaka-01"):
        parsed = configparse.parse(load(name))
        assert parsed.vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS), name


def test_nec_is_not_mistaken_for_cisco():
    assert configparse.parse(load("ix-rt-01")).vendor is Vendor.NEC_IX
    assert configparse.parse(load("qx-sw-01")).vendor is Vendor.NEC_QX


# ---------------------------------------------------------------------------
# 差分エンジンの方言
# ---------------------------------------------------------------------------


def test_comware_negation_is_undo_not_no():
    """QX は Comware なので、消すのは `no` ではなく `undo`。"""
    from app.services.configdiff import build

    current = "interface GigabitEthernet1/0/8\n description old\n port access vlan 10\n"
    target = "interface GigabitEthernet1/0/8\n description old\n"

    result = build(current, target, "nec_qx")
    assert result.verified
    assert "undo port access vlan 10" in result.script
    assert "no port access vlan" not in result.script


def test_ix_negation_is_no():
    from app.services.configdiff import build

    current = "interface GigaEthernet0.0\n description WAN\n ip address 10.0.0.1/30\n"
    target = "interface GigaEthernet0.0\n description WAN\n"

    result = build(current, target, "nec_ix")
    assert result.verified
    assert "no ip address 10.0.0.1/30" in result.script
