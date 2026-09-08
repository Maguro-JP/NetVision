"""Cisco Business / Small Business（SG・CBS）を、Catalyst とも他社とも取り違えないこと。

`vlan database` の行でアライドテレシスと取り違えていた。機器が印字する
`config-file-header` と版数の行、`show system` の型番から読む。
"""

from app.services import configparse
from app.services.configparse import Vendor

CONFIG = """\
config-file-header
sg350-01
v2.5.8.15 / RCBS3.1_930_412_016
CLI v1.0
file SSD indicator encrypted
@
!
unit-type-control-start
unit-type unit 1 network gi uplink te
unit-type-control-end
!
vlan database
vlan 10,20
exit
hostname sg350-01
ip ssh server
!
interface vlan 1
 ip address 192.168.1.254 255.255.255.0
!
interface vlan 10
 name USERS
!
interface GigabitEthernet1/0/1
 description uplink
 switchport mode trunk
 switchport trunk allowed vlan add 10,20
!
interface gi2
 switchport mode access
 switchport access vlan 10
!
interface Port-channel1
 switchport mode trunk
!
interface te1/0/1
 channel-group 1 mode auto
!
exit
ip default-gateway 192.168.1.1
"""

SHOW_SYSTEM = """\
sg350-01#show system
System Description:                       SG350-28P
System Up Time (days,hour:min:sec):       00,00:02:26
System Name:                              sg350-01
System Object ID:                         1.3.6.1.4.1.9.6.1.95.28.5

Unit     Type
---- ----------------------
1        SG350-28P
"""


def test_it_is_cisco_business_and_not_allied_telesis():
    parsed = configparse.parse(CONFIG)
    assert parsed.vendor is Vendor.CISCO_SB
    assert (parsed.hostname, parsed.os_version) == ("sg350-01", "2.5.8.15")
    # OS の名前は機器が印字しないので入れない
    assert parsed.os_name is None


def test_the_vlans_come_from_the_database_and_their_names_from_interface_vlan():
    parsed = configparse.parse(CONFIG)
    assert parsed.vlans == {10: "USERS", 20: "", 1: ""}
    vlan1 = next(p for p in parsed.interfaces if p.name == "Vlan1")
    assert vlan1.ip_address == "192.168.1.254/24"


def test_short_port_names_are_read_as_the_same_ports():
    parsed = configparse.parse(CONFIG)
    by_name = {p.name: p for p in parsed.interfaces}
    assert by_name["GigabitEthernet2"].vlan == 10
    assert by_name["TenGigabitEthernet1/0/1"].lag_group == 1
    assert by_name["GigabitEthernet1/0/1"].trunk_vlans == [10, 20]
    assert [r.next_hop for r in parsed.routes] == ["192.168.1.1"]


def test_the_model_comes_from_show_system():
    parsed = configparse.parse(CONFIG + "\n" + SHOW_SYSTEM)
    assert parsed.vendor is Vendor.CISCO_SB
    assert parsed.model == "SG350-28P"


def test_a_config_without_the_header_is_still_told_by_the_unit_type_line():
    text = "\n".join(["unit-type unit 1 network gi uplink none", "hostname sw", "interface gi1"])
    assert configparse.detect_vendor(text) is Vendor.CISCO_SB
