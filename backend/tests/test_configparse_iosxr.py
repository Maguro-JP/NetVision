"""Cisco IOS-XR を、IOS と同じ目で読まないこと。

同じ Cisco なので IOS の読み手をそのまま通ると、読めた形で通ってしまう。
住所も経路も抜けているのに、抜けたことに気づけないのがいちばん困る。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

XR = """!! IOS XR Configuration 7.5.2
hostname rtr-xr-01
!
interface GigabitEthernet0/0/0/0
 description to core-sw-01
 ipv4 address 10.0.0.1 255.255.255.0
!
interface GigabitEthernet0/0/0/1
 description spare
 shutdown
!
interface Bundle-Ether1
 ipv4 address 10.0.1.1/30
!
interface GigabitEthernet0/0/0/2
 ipv4 address dhcp
!
router ospf 1
 area 0
  interface GigabitEthernet0/0/0/0
  !
 !
!
router static
 address-family ipv4 unicast
  0.0.0.0/0 10.0.0.254
  10.9.0.0/16 GigabitEthernet0/0/0/0 10.0.0.253
 !
!
end
"""


def _parsed():
    return configparse.parse(XR)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_it_is_told_apart_from_ios() -> None:
    parsed = _parsed()
    assert parsed.vendor is Vendor.CISCO_XR
    assert parsed.os_version == "7.5.2"


def test_the_address_written_the_xr_way_is_read() -> None:
    """XR は `ipv4 address`。IOS の読み手は `ip address` しか見ていない。"""
    assert _port("GigabitEthernet0/0/0/0").ip_address == "10.0.0.1/24"


def test_an_address_written_with_a_length_is_read_too() -> None:
    assert _port("Bundle-Ether1").ip_address == "10.0.1.1/30"


def test_getting_the_address_from_dhcp_is_not_the_same_as_having_none() -> None:
    """空欄にすると、付け忘れと見分けが付かなくなる。"""
    port = _port("GigabitEthernet0/0/0/2")
    assert port.ip_address is None
    assert port.address_source == "dhcp"


def test_the_static_routes_are_read_although_there_is_no_ip_route_line() -> None:
    """XR は `router static` の囲みの中に、宛先と次の先だけを並べる。"""
    routes = {route.destination: route for route in _parsed().routes}
    assert routes["0.0.0.0/0"].next_hop == "10.0.0.254"
    assert routes["10.9.0.0/16"].next_hop == "10.0.0.253"
    assert routes["10.9.0.0/16"].interface == "GigabitEthernet0/0/0/0"


def test_which_ports_are_in_which_ospf_area_is_read() -> None:
    """XR は area の囲みの中に口を並べる。IOS の network 行が無い。"""
    parsed = _parsed()
    assert parsed.ospf is not None
    assert [(area.area, area.networks) for area in parsed.ospf.areas] == [
        ("0", ["GigabitEthernet0/0/0/0"])
    ]


def test_a_port_that_is_shut_is_still_read_as_shut() -> None:
    assert _port("GigabitEthernet0/0/0/1").enabled is False


def test_an_ios_config_is_not_taken_over() -> None:
    ios = (
        "hostname sw-01\n!\ninterface GigabitEthernet0/1\n"
        " ip address 10.0.0.1 255.255.255.0\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.254\n"
    )
    assert configparse.parse(ios).vendor is Vendor.CISCO_IOS


XR_PE = """\
RP/0/RP0/CPU0:pe1#show running-config
hostname pe1
Cisco IOS XR Software, Version 7.3.2
cisco NCS-5500 () processor
vrf CUST-A
 address-family ipv4 unicast
  import route-target
   65000:100
interface Loopback0
 ipv4 address 10.255.0.1 255.255.255.255
interface GigabitEthernet0/0/0/1.100
 vrf CUST-A
 ipv4 address 192.168.100.1 255.255.255.0
router bgp 65000
 bgp router-id 10.255.0.1
 address-family vpnv4 unicast
 neighbor 10.255.0.2
  remote-as 65000
  description to pe2
  update-source Loopback0
  address-family vpnv4 unicast
   route-policy PASS in
 vrf CUST-A
  rd 65000:100
  address-family ipv4 unicast
   redistribute connected
  neighbor 192.168.100.2
   remote-as 65100
   address-family ipv4 unicast
"""


def test_the_xr_model_is_read_from_the_show_version_line():
    """NCS 5500 の show version は `cisco NCS-5500 () processor` と印字する。"""
    assert configparse.parse(XR_PE).model == "NCS-5500"


def test_the_vrf_block_and_the_interface_vrf_are_read():
    parsed = configparse.parse(XR_PE)
    assert [(v.name, v.rd) for v in parsed.vrfs] == [("CUST-A", "65000:100")]
    port = next(p for p in parsed.interfaces if p.name == "GigabitEthernet0/0/0/1.100")
    assert port.vrf == "CUST-A"


def test_the_nested_bgp_neighbors_of_xr_are_read():
    """XR は相手ごとの囲みに remote-as や address-family を書く。IOS の1行書きではない。"""
    bgp = configparse.parse(XR_PE).bgp
    assert (bgp.local_as, bgp.router_id) == ("65000", "10.255.0.1")
    by_address = {n.address: n for n in bgp.neighbors}
    pe2 = by_address["10.255.0.2"]
    assert (pe2.remote_as, pe2.description, pe2.update_source) == ("65000", "to pe2", "Loopback0")
    assert (pe2.address_families, pe2.activated, pe2.route_map_in) == (
        ["vpnv4 unicast"],
        True,
        "PASS",
    )
    ce = by_address["192.168.100.2"]
    assert (ce.remote_as, ce.address_families) == ("65100", ["ipv4 unicast"])


def test_isis_lists_the_interfaces_it_carries():
    text = (
        "RP/0/RP0/CPU0:pe1#show running-config\nhostname pe1\n"
        "router isis CORE\n net 49.0001.0000.0000.0001.00\n"
        " interface HundredGigE0/0/0/0\n  address-family ipv4 unicast\n"
    )
    isis = configparse.parse(text).protocols[0]
    assert (isis.name, isis.networks) == ("isis", ["interface HundredGigE0/0/0/0"])


def test_xr_records_bgp_neighbor_changes_unless_told_not_to():
    """XR は既定で記録する。IOS と同じ「入れていない」扱いにすると、無い指摘が出る。"""
    head = "RP/0/RP0/CPU0:pe1#show running-config\nhostname pe1\nrouter bgp 65000\n"
    body = " neighbor 10.0.0.2\n  remote-as 65000\n"
    assert configparse.parse(head + body).bgp.log_neighbor_changes is True
    off = head + " bgp log neighbor changes disable\n" + body
    assert configparse.parse(off).bgp.log_neighbor_changes is False
