"""ベンダーの判別。取り違えると、そこから先がすべて外れる。

読み違いは画面に出ない。別ベンダーとして読まれた Config は、マスクが落ち、
冗長も経路も読めず、指摘だけがそれらしく並ぶ。ここは一度崩れると
気づけないので、ベンダーごとに1つずつ形を固定しておく。
"""

from app.services import configparse
from app.services.configparse import Vendor

SAMPLES: dict[Vendor, str] = {
    Vendor.CISCO_IOS: """hostname sw
interface GigabitEthernet1/0/1
 switchport mode trunk
 switchport trunk allowed vlan 10,20
interface Vlan10
 ip address 10.0.10.1 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.0.10.254
""",
    Vendor.CISCO_NXOS: """hostname n9k
feature hsrp
interface Ethernet1/1
  ip address 10.1.1.1/30
vpc domain 1
  peer-keepalive destination 10.255.0.2 source 10.255.0.1
""",
    Vendor.CISCO_ASA: """hostname fw
interface GigabitEthernet0/0
 nameif outside
 security-level 0
 ip address 203.0.113.2 255.255.255.248
access-list OUT extended permit tcp any host 203.0.113.10 eq https
""",
    Vendor.JUNIPER: """set system host-name edge-rt-01
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set protocols bgp group EXT peer-as 65002
""",
    Vendor.FORTINET: """config system interface
    edit "port1"
        set ip 203.0.113.2 255.255.255.248
    next
end
""",
    Vendor.NEC_IX: """hostname ix-rt-01
!
interface GigaEthernet0.0
  ip address 10.1.0.1/24
  no shutdown
!
ip route default GigaEthernet0.0 10.1.0.254
""",
    Vendor.NEC_QX: """sysname qx-sw-01
interface GigabitEthernet1/0/1
 port link-type trunk
 port trunk permit vlan 10 20
""",
    Vendor.YAMAHA: """ip lan1 address 192.168.100.1/24
ip filter 200031 reject * * tcp * 23
pp select 1
""",
    Vendor.PALO_ALTO: """set network interface ethernet ethernet1/1 layer3 ip 10.0.0.1/24
set deviceconfig system hostname pa-fw-01
""",
    Vendor.ARISTA: """! device: arista-sw-01 (DCS-7050SX3-48YC8, EOS-4.28.3M)
!
hostname arista-sw-01
interface Ethernet1
   no switchport
   ip address 10.1.1.1/30
""",
    Vendor.ARUBA_CX: """hostname cx-01
vlan trunk allowed 10,20
interface vlan 20
    ip address 10.0.20.2/24
""",
    Vendor.CISCO_XR: """!! IOS XR Configuration 7.5.2
hostname rtr-xr-01
interface GigabitEthernet0/0/0/0
 ipv4 address 10.0.0.1 255.255.255.0
""",
    Vendor.ARUBA_OS: """hostname "sw-aoss-01"
module 1 type jl253a
vlan 10
   name "USERS"
   untagged 1-20
   exit
""",
    Vendor.ALLIED: """hostname at-sw-01
interface port1.0.1
 switchport mode trunk
vlan database
 vlan 10 name USERS
""",
    Vendor.ALAXALA: """Model AX3650S-24T4XW
S/W: OS-L3A Ver. 12.1.A
hostname "ax-sw-01"
interface gigabitethernet 0/1
  switchport mode trunk
""",
    Vendor.HUAWEI: """Huawei Versatile Routing Platform Software
VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)
sysname hw-sw-01
#
interface 10GE1/0/1
 port link-type trunk
""",
    Vendor.FASTIRON: """  HW: Stackable ICX7150-48P
  SW: Version 08.0.95cT213
hostname icx-sw-01
vlan 10 name USERS by port
 tagged ethe 1/1/1
""",
    Vendor.CISCO_WLC: """Product Version.................................. 8.10.185.0
System Name...................................... wlc-01
IP Address....................................... 10.0.0.31
""",
}


def test_every_vendor_has_a_sample() -> None:
    """読み手を足したら、見本も足す。

    見本が無いベンダーは、他の見本を横取りしていても気づけない。
    """
    missing = {vendor for vendor in Vendor if vendor is not Vendor.UNKNOWN} - set(SAMPLES)
    assert not missing, missing


def test_each_vendor_is_recognised_from_its_own_way_of_writing() -> None:
    for vendor, text in SAMPLES.items():
        assert configparse.parse(text).vendor is vendor, vendor


def test_no_sample_is_claimed_by_another_vendor() -> None:
    """1つの見本が2つのベンダーに当たると、順番で結果が変わる。"""
    for vendor, text in SAMPLES.items():
        assert configparse.detect_vendor(text) is vendor, vendor


def test_a_config_with_no_vendor_marker_is_not_guessed() -> None:
    """手掛かりが無いものを、それらしいベンダーに寄せない。

    別ベンダーとして読むより、読めないと言うほうが害が小さい。
    """
    assert configparse.parse("この機器は先週入れ替えました\n").vendor is Vendor.UNKNOWN


def test_the_screen_knows_every_vendor_the_reader_can_name() -> None:
    """画面の型に、読み手が名乗るベンダーが全部あること。

    足し忘れると、画面側の型と実態が食い違ったままになる。落ちはしないので
    気づけない。
    """
    import pathlib
    import re

    types = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts"
    text = types.read_text(encoding="utf-8")
    block = text[text.index("export type ConfigVendor") :]
    block = block[: block.index(";")]
    listed = set(re.findall(r'"([a-z_0-9]+)"', block))
    assert {vendor.value for vendor in Vendor} <= listed
