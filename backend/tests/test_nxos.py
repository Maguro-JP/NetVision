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


def test_nxos_static_routes_are_read() -> None:
    """NX-OS は長さで書く（ip route 0.0.0.0/0 …）。

    この形を読んでいないと、静的経路が丸ごと落ちる。既定経路も落ちるので、
    書いてある機器に「既定の経路が見当たりません」を出すことになる。
    """
    text = "\n".join(
        [
            "hostname n9k",
            "feature bgp",
            "interface Ethernet1/1",
            "  ip address 10.0.0.1/24",
            "ip route 0.0.0.0/0 10.0.0.254",
            "ip route 10.9.0.0/16 10.0.0.253 name BRANCH 200",
            "ip route vrf CUST 192.168.5.0/24 192.168.5.254",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is configparse.Vendor.CISCO_NXOS
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "10.0.0.254"
    # name の後ろの語を距離と取り違えない
    assert routes["10.9.0.0/16"].distance == 200
    assert routes["192.168.5.0/24"].vrf == "CUST"


def test_the_ios_way_of_writing_routes_still_works() -> None:
    """点で区切ったマスクの形は、これまで通り読める。"""
    text = "\n".join(
        [
            "hostname r1",
            "interface Vlan10",
            " ip address 10.0.0.1 255.255.255.0",
            "ip route 0.0.0.0 0.0.0.0 10.0.0.254",
            "ip route 10.20.0.0 255.255.0.0 10.0.0.253",
        ]
    )
    routes = {r.destination for r in configparse.parse(text).routes}
    assert routes == {"0.0.0.0/0", "10.20.0.0/16"}


def test_vrf_context_and_its_routes_are_read() -> None:
    """NX-OS の VRF は `vrf context`。IOS の書き方だけを見ていると丸ごと落ちる。

    VRF の中の経路を全体の経路表に混ぜると、「既定の経路がある」と誤って
    読むことになる。
    """
    text = "\n".join(
        [
            "version 9.3(10)",
            "hostname n9k-01",
            "vrf context MGMT",
            "  ip route 0.0.0.0/0 10.99.0.1",
            "interface mgmt0",
            "  vrf member management",
            "  ip address 10.99.0.5/24",
            "interface Vlan10",
            "  ip address 10.0.10.2/24",
        ]
    )
    parsed = configparse.parse(text)
    assert "MGMT" in {vrf.name for vrf in parsed.vrfs}
    ports = {item.name: item for item in parsed.interfaces}
    assert ports["Management0"].vrf == "management"
    assert [(route.destination, route.vrf) for route in parsed.routes] == [("0.0.0.0/0", "MGMT")]


def test_a_global_route_is_not_pulled_into_a_vrf() -> None:
    text = "\n".join(
        [
            "version 9.3(10)",
            "hostname n9k-01",
            "vrf context MGMT",
            "  ip route 0.0.0.0/0 10.99.0.1",
            "ip route 10.1.0.0/16 10.0.0.2",
        ]
    )
    routes = {route.destination: route.vrf for route in configparse.parse(text).routes}
    assert routes["10.1.0.0/16"] is None
    assert routes["0.0.0.0/0"] == "MGMT"


def test_the_nested_hsrp_block_is_read_whatever_order_its_lines_come_in():
    """`preempt` や `timers` が先に来ても、仮想アドレスと優先度を読む。

    出典: Nexus 9000 NX-OS Unicast Routing Configuration Guide「Configuring HSRP」の例は
    `hsrp 0` → `preempt` → `priority` → `ip` の順で書かれている。

    塊の行は字下げを落として渡ってくるので、知らない行で塊を閉じると、
    その後ろの `ip` が読めない。`ip address`（口自身のアドレス）は読まない。
    """
    text = "\n".join(
        [
            "hostname n9k",
            "interface Vlan10",
            "  hsrp version 2",
            "  hsrp 10",
            "    preempt",
            "    timers 1 3",
            "    priority 110",
            "    ip 10.10.0.1",
            "  ip address 10.10.0.2/24",
            "  no shutdown",
        ]
    )
    parsed = configparse.parse(text)
    entry = parsed.redundancy[0]
    assert (entry.virtual_ip, entry.priority) == ("10.10.0.1", 110)
    assert parsed.interfaces[0].ip_address == "10.10.0.2/24"
    assert [f.name for f in parsed.features if f.category == "high_availability"] == ["HSRP"]


def test_the_address_family_nested_under_a_neighbor_belongs_to_that_neighbor():
    """NX-OS は相手ごとに `address-family ipv4 unicast` を入れ子で書く。"""
    text = "\n".join(
        [
            "hostname n9k",
            "feature bgp",
            "router bgp 65000",
            "  router-id 10.255.0.1",
            "  neighbor 10.1.1.0 remote-as 65100",
            "    address-family ipv4 unicast",
            "  neighbor 10.1.1.2 remote-as 65100",
            "    address-family ipv4 unicast",
            "    address-family ipv6 unicast",
        ]
    )
    bgp = configparse.parse(text).bgp
    first, second = bgp.neighbors
    assert (first.address_families, first.activated) == (["ipv4 unicast"], True)
    assert second.address_families == ["ipv4 unicast", "ipv6 unicast"]


def test_vpc_is_listed_as_a_high_availability_feature():
    text = "\n".join(
        [
            "hostname n9k",
            "vpc domain 10",
            "  peer-keepalive destination 10.0.0.2 source 10.0.0.1",
            "interface port-channel1",
            "  vpc peer-link",
        ]
    )
    names = [f.name for f in configparse.parse(text).features if f.category == "high_availability"]
    assert names == ["vPC"]
