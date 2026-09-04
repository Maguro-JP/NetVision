"""NX-OS の書き方。本社のコアに多いので、ここを読み落とすと中心が見えない。"""

from app.services import configparse, policyreview
from app.services.reviewnote import HIGH

NX = """hostname nx-core-01
!
feature interface-vlan
feature lacp
feature vpc
!
vlan 10
  name USERS
vlan 20
  name SERVERS
!
vpc domain 1
  peer-switch
  role priority 1000
  peer-keepalive destination 10.0.0.12 source 10.0.0.11
  peer-gateway
!
interface port-channel1
  description vpc peer-link
  switchport mode trunk
  switchport trunk allowed vlan 10,20
  vpc peer-link
!
interface Ethernet1/1
  description to nx-core-02
  switchport mode trunk
  switchport trunk allowed vlan 10,20
  channel-group 1 mode active
!
interface Vlan10
  no shutdown
  ip address 10.0.10.2/24
  hsrp 10
    ip 10.0.10.1
    priority 110
    preempt
!
"""


def test_the_gateway_redundancy_written_in_nested_blocks_is_read():
    """NX-OS は `hsrp 10` の下に字下げで書く。IOS の1行書きとは形が違う。"""
    entries = configparse.parse(NX).redundancy
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.interface, entry.group, entry.protocol) == ("Vlan10", "10", "hsrp")
    assert entry.virtual_ip == "10.0.10.1"
    assert entry.priority == 110


def test_a_virtual_address_outside_the_segment_is_caught_here_too():
    text = NX.replace("    ip 10.0.10.1", "    ip 10.0.99.1")
    found = [f for f in policyreview.review(configparse.parse(text), text) if "仮想" in f.title]
    assert len(found) == 1
    assert found[0].level == HIGH


def test_the_pair_is_read_as_one_pair():
    pair = configparse.parse(NX).ha_pair
    assert pair.kind == "vpc"
    assert pair.group == "1"
    assert pair.peer_address == "10.0.0.12"
    assert pair.priority == 1000
    assert pair.link == "port-channel1"


def test_the_ports_and_vlans_are_read():
    parsed = configparse.parse(NX)
    assert parsed.vendor == "cisco_nxos"
    assert parsed.vlans == {10: "USERS", 20: "SERVERS"}
    ports = {item.name: item for item in parsed.interfaces}
    assert ports["Ethernet1/1"].lag_group == 1
    assert ports["Port-channel1"].trunk_vlans == [10, 20]
    assert ports["Vlan10"].ip_address == "10.0.10.2/24"


def test_nxos_is_recognised_without_the_word_feature() -> None:
    """feature を1つも使っていない設定でも、NX-OS の書き方で見分ける。"""
    text = "\n".join(
        [
            "hostname n9k",
            "interface Vlan100",
            "  ip address 10.100.0.2/24",
            "  hsrp version 2",
            "  hsrp 100",
            "    ip 10.100.0.1",
            "vpc domain 1",
            "  peer-keepalive destination 10.255.0.2 source 10.255.0.1",
        ]
    )
    assert configparse.parse(text).vendor is configparse.Vendor.CISCO_NXOS


def test_a_catalyst_config_is_still_ios() -> None:
    """standby や switchport だけの設定を NX-OS に寄せてしまわない。"""
    text = "\n".join(
        [
            "hostname c3850",
            "interface GigabitEthernet1/0/1",
            " switchport mode access",
            " standby 1 ip 10.0.0.1",
        ]
    )
    assert configparse.parse(text).vendor is configparse.Vendor.CISCO_IOS
