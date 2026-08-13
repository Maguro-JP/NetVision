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
    assert parsed.serial_number == "210235A0L6C123000010"


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


def test_qx_running_config_with_sysname_is_enough():
    """running-config だけ（バナーも型番も無し）でも sysname で機器名が取れること。

    現場では display current-configuration だけが来る。hostname コマンドは無く、
    機器名は sysname に入っている。ここを Cisco 扱いすると台帳と結び付かない。
    """
    text = """
#
 sysname osaka-qx-01
#
interface GigabitEthernet1/0/1
 port link-type trunk
 port trunk permit vlan 10
#
interface Vlan-interface10
 ip address 192.168.10.254 255.255.255.0
#
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "osaka-qx-01"
    assert parsed.serial_number is None
    assert any(i.name == "GigabitEthernet1/0/1" for i in parsed.interfaces)


def test_qx_serial_from_esn_and_sentence_forms():
    esn = configparse.parse(
        "sysname qx-sw-01\ninterface GigabitEthernet1/0/1\nESN of slot 1: 210235A0L6C123000010\n"
    )
    assert esn.serial_number == "210235A0L6C123000010"

    sentence = configparse.parse(
        "sysname qx-sw-01\ninterface GigabitEthernet1/0/1\n"
        "The Serial Number of the device is: 210235A0L6C999000001\n"
    )
    assert sentence.serial_number == "210235A0L6C999000001"


def test_qx_serial_skips_none_and_power_supply():
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
Slot 1:
DEVICE_SERIAL_NUMBER : None
Power 1:
DEVICE_SERIAL_NUMBER : 210231A0PWR0000001
Chassis:
DEVICE_SERIAL_NUMBER : 210235A0L6C123000010
"""
    parsed = configparse.parse(text)
    assert parsed.serial_number == "210235A0L6C123000010"


def test_qx_does_not_treat_lldp_chassis_id_as_serial():
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  Chassis type      : MAC address
  Chassis ID        : 0011-2233-4455
  System name       : core-sw-01
  Port ID           : GigabitEthernet1/0/24
"""
    parsed = configparse.parse(text)
    assert parsed.serial_number is None


def test_qx_reads_model_from_manuinfo_when_banner_is_missing():
    text = """
sysname branch-qx-01
interface GigabitEthernet1/0/1
Slot 1:
DEVICE_NAME          : QX-S6520X
DEVICE_SERIAL_NUMBER : 210235A1ABC123456789
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.model == "QX-S6520X"
    assert parsed.serial_number == "210235A1ABC123456789"


def test_quoted_sysname_is_read():
    parsed = configparse.parse('sysname "branch-sw-01"\ninterface GigabitEthernet1/0/1\n')
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "branch-sw-01"


def test_ix_lldp_reads_port_id_even_when_it_comes_before_system_name():
    """IX の detail は Port ID が System Name より先。順番に依存しないこと。"""
    text = """
IX Series IX2215 (magellan-sec) Software, Version 10.6.21
device-name ix-rt-01
!
interface GigaEthernet1.0
  no shutdown
!
Interface: GigaEthernet1.0
  Chassis ID subtype: MAC address
  Chassis ID: 0012.e200.1234
  Port ID subtype: Interface name
  Port ID: GigabitEthernet1/0/24
  System Name: core-sw-01
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_IX
    assert parsed.hostname == "ix-rt-01"
    assert len(parsed.neighbors) == 1
    assert parsed.neighbors[0].local_interface == "GigaEthernet1.0"
    assert parsed.neighbors[0].remote_hostname == "core-sw-01"
    assert parsed.neighbors[0].remote_interface == "GigabitEthernet1/0/24"


def test_qx_lldp_uses_port_description_when_port_id_is_a_mac():
    """Port ID が MAC のとき、ポート名は description 側にある。"""
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  Chassis type      : MAC address
  Port ID           : 0011-2233-4455
  Port description  : Ten-GigabitEthernet1/0/1
  System name       : core-sw-01
"""
    parsed = configparse.parse(text)
    assert parsed.neighbors[0].remote_hostname == "core-sw-01"
    assert parsed.neighbors[0].remote_interface == "TenGigabitEthernet1/0/1"


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
