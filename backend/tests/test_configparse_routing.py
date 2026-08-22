"""L3（経路・OSPF・BGP・ゲートウェイ冗長）の読み取り。

ここが読めていないと、機器を1台足すときに何を書けばいいのかも、
いまの構成のどこが危ういのかも言えない。ベンダーごとに書き方は違うが、
意味は同じところへ落ちることを確かめる。
"""

from app.services import configparse
from app.services.configparse.routing import to_prefix

IOS = """hostname core-sw-01
!
interface Vlan10
 ip address 10.10.10.2 255.255.255.0
 ip ospf 1 area 0
 standby 10 ip 10.10.10.1
 standby 10 priority 110
!
router ospf 1
 router-id 10.0.0.1
 network 10.10.10.0 0.0.0.255 area 0
 passive-interface default
 no passive-interface Vlan10
 redistribute static
!
router bgp 65001
 bgp router-id 10.0.0.1
 neighbor 203.0.113.1 remote-as 65000
 neighbor 203.0.113.1 description ISP-A
 network 10.10.0.0 mask 255.255.0.0
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
ip route 192.168.20.0 255.255.255.0 10.10.10.253 250
ip route vrf MGMT 172.16.0.0 255.255.0.0 172.16.0.254
"""


def test_the_two_ways_of_writing_a_mask_land_on_the_same_prefix():
    """ワイルドカードもネットマスクも同じ形にそろえる。そろわないと後で照合できない。"""
    assert to_prefix("10.10.10.0", "255.255.255.0") == "10.10.10.0/24"
    assert to_prefix("10.10.10.0", "0.0.0.255") == "10.10.10.0/24"
    assert to_prefix("10.10.10.0/24") == "10.10.10.0/24"
    assert to_prefix("そんなIPは無い") is None


def test_static_routes_are_read_with_their_distance_and_vrf():
    parsed = configparse.parse(IOS)
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "10.10.10.254"
    assert routes["0.0.0.0/0"].is_default
    assert routes["192.168.20.0/24"].distance == 250
    assert routes["172.16.0.0/16"].vrf == "MGMT"


def test_ospf_keeps_which_area_each_network_is_in():
    parsed = configparse.parse(IOS)
    assert parsed.ospf is not None
    assert parsed.ospf.process_id == "1"
    assert parsed.ospf.router_id == "10.0.0.1"
    assert parsed.ospf.area_of("10.10.10.0/24") == "0"
    assert parsed.ospf.passive_default is True
    assert "Vlan10" in parsed.ospf.passive_interfaces
    assert parsed.ospf.redistributes == ["static"]


def test_the_area_written_on_the_interface_is_read_too():
    """NX-OS と IOS-XE は、エリアをインターフェース側に書く。"""
    parsed = configparse.parse(IOS)
    assert parsed.ospf.area_of("interface Vlan10") == "0"


def test_bgp_keeps_the_peer_and_its_as():
    parsed = configparse.parse(IOS)
    assert parsed.bgp is not None
    assert parsed.bgp.local_as == "65001"
    neighbor = parsed.bgp.neighbors[0]
    assert (neighbor.address, neighbor.remote_as) == ("203.0.113.1", "65000")
    assert neighbor.description == "ISP-A"
    assert parsed.bgp.networks == ["10.10.0.0/16"]


def test_the_gateway_redundancy_is_read_per_interface():
    parsed = configparse.parse(IOS)
    assert len(parsed.redundancy) == 1
    entry = parsed.redundancy[0]
    assert (entry.protocol, entry.group, entry.virtual_ip, entry.priority) == (
        "hsrp",
        "10",
        "10.10.10.1",
        110,
    )


QX = """sysname edge-sw-01
#
ospf 1 router-id 10.0.0.2
 area 0.0.0.0
  network 10.20.0.0 0.0.255.255
#
bgp 65002
 router-id 10.0.0.2
 peer 10.0.0.1 as-number 65001
#
interface Vlan-interface20
 ip address 10.20.0.2 255.255.255.0
 vrrp vrid 20 virtual-ip 10.20.0.1
 vrrp vrid 20 priority 120
#
 ip route-static 0.0.0.0 0.0.0.0 10.20.0.254
"""


def test_comware_writes_it_differently_but_means_the_same():
    parsed = configparse.parse(QX)
    assert parsed.vendor is configparse.Vendor.NEC_QX
    assert parsed.routes[0].destination == "0.0.0.0/0"
    assert parsed.routes[0].next_hop == "10.20.0.254"
    assert parsed.ospf.area_of("10.20.0.0/16") == "0.0.0.0"
    assert parsed.bgp.local_as == "65002"
    assert parsed.bgp.neighbors[0].remote_as == "65001"
    assert parsed.redundancy[0].protocol == "vrrp"
    assert parsed.redundancy[0].virtual_ip == "10.20.0.1"
    assert parsed.redundancy[0].priority == 120


JUNOS = """set system host-name edge-rt-01
set routing-options static route 0.0.0.0/0 next-hop 198.51.100.1
set routing-options autonomous-system 65003
set protocols ospf area 0.0.0.0 interface ge-0/0/0.0
set protocols bgp group EXTERNAL neighbor 198.51.100.1 peer-as 65000
"""


def test_junos_set_lines_are_read():
    parsed = configparse.parse(JUNOS)
    assert parsed.routes[0].destination == "0.0.0.0/0"
    assert parsed.routes[0].next_hop == "198.51.100.1"
    assert parsed.ospf.area_of("interface ge-0/0/0.0") == "0.0.0.0"
    assert parsed.bgp.local_as == "65003"
    assert parsed.bgp.neighbors[0].remote_as == "65000"


FORTIOS = """config system global
    set hostname "fw-01"
end
config router static
    edit 1
        set gateway 203.0.113.254
        set device "wan1"
    next
    edit 2
        set dst 10.30.0.0 255.255.0.0
        set gateway 10.30.0.254
        set distance 15
    next
end
"""


def test_fortios_edit_blocks_are_read():
    parsed = configparse.parse(FORTIOS)
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "203.0.113.254"
    assert routes["0.0.0.0/0"].interface == "wan1"
    assert routes["10.30.0.0/16"].distance == 15


IX = """device-name branch-rt-01
ip route 0.0.0.0/0 192.0.2.254
ip route 10.40.0.0/16 192.0.2.253 200
"""


def test_nec_ix_writes_the_prefix_with_a_slash():
    parsed = configparse.parse(IX)
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "192.0.2.254"
    assert routes["10.40.0.0/16"].distance == 200


def test_a_config_without_l3_leaves_it_empty_rather_than_guessing():
    parsed = configparse.parse(
        "hostname access-sw-01\n!\ninterface Gi1/0/1\n switchport mode access\n"
    )
    assert parsed.routes == []
    assert parsed.ospf is None
    assert parsed.bgp is None
    assert parsed.redundancy == []


# ---------------------------------------------------------------------------
# OSPF / BGP 以外の経路プロトコルと、経路を選ぶ道具
# ---------------------------------------------------------------------------

OTHERS = """hostname core-rt-01
!
router eigrp 100
 network 10.10.0.0 0.0.255.255
 passive-interface Vlan10
 redistribute static
!
router rip
 network 192.168.0.0
!
router isis CORE
 net 49.0001.0000.0000.0001.00
!
route-map TO-ISP permit 10
 match ip address prefix-list ADVERTISE
!
ip prefix-list ADVERTISE seq 5 permit 10.10.0.0/16
!
route-map UNUSED permit 10
!
interface Vlan20
 ip policy route-map BRANCH-OUT
!
route-map BRANCH-OUT permit 10
!
router bgp 65001
 neighbor 203.0.113.1 remote-as 65000
 neighbor 203.0.113.1 route-map TO-ISP out
"""


def test_the_protocols_still_running_in_the_field_are_read():
    """EIGRP や RIP が読めないと、経路がどこから来ているのか説明できない。"""
    parsed = configparse.parse(OTHERS)
    found = {p.name: p for p in parsed.protocols}
    assert found["eigrp"].identifier == "100"
    assert found["eigrp"].networks == ["10.10.0.0/16"]
    assert found["eigrp"].passive_interfaces == ["Vlan10"]
    assert found["eigrp"].redistributes == ["static"]
    assert found["isis"].router_id == "49.0001.0000.0000.0001.00"


def test_a_network_without_a_mask_is_not_read_as_one_address():
    """書いていないものを /32 と読むと、1台ぶんの意味になってしまう。"""
    parsed = configparse.parse(OTHERS)
    rip = next(p for p in parsed.protocols if p.name == "rip")
    assert rip.networks == ["192.168.0.0/24"]


def test_the_tools_that_pick_routes_are_kept_with_where_they_are_used():
    """「広告しているはずなのに届かない」の原因はたいていここにある。"""
    parsed = configparse.parse(OTHERS)
    policies = {p.name: p for p in parsed.policies}
    assert "BGP" in policies["TO-ISP"].used_by
    assert "route-map TO-ISP" in policies["ADVERTISE"].used_by
    assert policies["BRANCH-OUT"].kind == "policy-based-routing"
    assert "interface Vlan20" in policies["BRANCH-OUT"].used_by


def test_one_that_nobody_uses_is_left_visible():
    parsed = configparse.parse(OTHERS)
    unused = next(p for p in parsed.policies if p.name == "UNUSED")
    assert unused.used_by == []
