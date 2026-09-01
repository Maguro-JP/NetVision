"""Aruba CX（AOS-CX）の config を読む。

IOS に似ているが、ポートの書き方が違う。とくに「書かなければ落ちている」
ところを取り違えると、使われていないポートまで生きていることになる。
"""

from pathlib import Path

import pytest

from app.services import configparse
from app.services.configparse import Vendor

FIXTURE = Path(__file__).parent / "fixtures" / "aruba-cx-01.txt"


@pytest.fixture()
def parsed():
    return configparse.parse(FIXTURE.read_text(encoding="utf-8"))


def test_it_is_recognised_as_aruba_cx(parsed):
    assert parsed.vendor is Vendor.ARUBA_CX
    assert parsed.os_name == "AOS-CX"
    assert parsed.os_version == "FL.10.09.0010"
    assert parsed.model == "6300M-48G-4SFP56"
    assert parsed.hostname == "sw-aruba-01"


def test_a_port_without_no_shutdown_is_down(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["1/1/10"].enabled is True
    # `no shutdown` を書いていないので落ちている。IOS の目だと見落とす
    assert ports["1/1/11"].enabled is False


def test_the_vlan_of_a_port_is_read(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["1/1/10"].vlan == 20
    assert ports["1/1/10"].mode == "access"
    assert ports["1/1/24"].vlan == 100


def test_a_trunk_lists_only_what_is_allowed(parsed):
    lag = {i.name: i for i in parsed.interfaces}["lag 1"]
    assert lag.mode == "trunk"
    assert lag.trunk_vlans == [10, 20]
    assert lag.trunk_all is False
    assert lag.native_vlan == 1
    assert lag.lag_mode == "active"


def test_the_members_of_a_lag_point_at_it(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["1/1/1"].lag_group == 1
    assert ports["1/1/2"].lag_group == 1


def test_addresses_are_read_with_their_length(parsed):
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["Vlan10"].ip_address == "10.0.10.1/24"
    assert ports["Vlan100"].ip_address == "10.0.100.1/24"
    assert ports["Vlan100"].vrf == "GUEST"


def test_ospf_settings_on_a_port_are_read(parsed):
    port = {i.name: i for i in parsed.interfaces}["Vlan10"]
    assert port.ospf_area == "0.0.0.0"
    assert port.ospf_hello == 10
    assert port.ospf_dead == 40


def test_routes_are_read_with_their_length(parsed):
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "10.0.10.254"
    assert routes["10.9.0.0/16"].distance == 200


def test_the_vlans_are_listed(parsed):
    assert parsed.vlans[10] == "USERS"
    assert parsed.vlans[20] == "SERVERS"
    assert parsed.vlans[100] == "GUEST"


def test_the_acl_is_read_in_order(parsed):
    acl = {a.name: a for a in parsed.acls}["WEB-IN"]
    assert [e.seq for e in acl.entries] == ["10", "20", "30"]
    assert acl.entries[0].destination == "10.0.20.0/24"
    assert acl.entries[0].destination_port == "eq 443"
    assert acl.entries[1].log is True
    assert acl.entries[2].action == "deny"


def test_where_an_acl_is_applied_is_read(parsed):
    acls = {a.name: a for a in parsed.acls}
    assert acls["WEB-IN"].applied_to == ["interface vlan 20 in"]
    assert acls["GUEST-IN"].applied_to == ["interface 1/1/24 in"]


def test_bgp_is_still_read_by_the_ios_side(parsed):
    assert parsed.bgp is not None
    assert parsed.bgp.local_as == "65001"
    assert [n.address for n in parsed.bgp.neighbors] == ["10.0.10.2"]
