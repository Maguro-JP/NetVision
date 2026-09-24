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
    # VLAN の口は作った時点で上がる。物理ポートと同じ目で見ると
    # 「アドレスが振ってあるのに止めてある」と誤って言うことになる
    assert ports["Vlan10"].enabled is True


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
    # 向きの言葉は他のベンダーとそろえてある（表に並べたときに混ざらないように）
    assert acls["WEB-IN"].applied_to == ["interface vlan 20 入り"]
    assert acls["GUEST-IN"].applied_to == ["interface 1/1/24 入り"]


def test_bgp_is_still_read_by_the_ios_side(parsed):
    assert parsed.bgp is not None
    assert parsed.bgp.local_as == "65001"
    assert [n.address for n in parsed.bgp.neighbors] == ["10.0.10.2"]


def test_the_lldp_table_gives_the_lines_for_the_diagram(parsed):
    """`show lldp neighbor-info` の表。PORT-DESC に空白が入っていても崩れない。"""
    pairs = [(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors]
    assert ("1/1/1", "core-aruba-01", "1/1/24") in pairs
    assert ("1/1/2", "core-sw-01", "GigabitEthernet1/0/1") in pairs


OFFICIAL_REDUNDANCY = """\
hostname aruba-cx-01
vlan 10
    name USERS
vlan 20
    name SERVERS
interface vlan 10
    ip address 192.168.1.253/16
    no shutdown
    vrrp 1 address-family ipv4
        address 192.168.1.250 primary
        priority 110
        no shutdown
interface vlan 20
    ip address 192.168.100.5/16
    active-gateway ip 192.168.100.2 mac 00:00:00:00:00:01
"""


def test_the_gateway_redundancy_of_aos_cx_is_read():
    """出典: AOS-CX の「VRRP with VSX configuration」の設定例
    （`vrrp 1 address-family ipv4` の下に `address … primary`）と、
    VSX ガイドの `active-gateway ip 192.168.100.2 mac 00:00:00:00:00:01`。

    どちらも端末の出口を冗長にする仕組み。読まないと、出口の冗長が
    資料から丸ごと消える。
    """
    parsed = configparse.parse(OFFICIAL_REDUNDANCY)
    entries = {entry.protocol: entry for entry in parsed.redundancy}
    vrrp = entries["vrrp"]
    assert (vrrp.interface, vrrp.group, vrrp.virtual_ip, vrrp.priority) == (
        "Vlan10",
        "1",
        "192.168.1.250",
        110,
    )
    gateway = entries["active-gateway"]
    # active-gateway はグループ番号を持たない
    assert (gateway.interface, gateway.group, gateway.virtual_ip) == (
        "Vlan20",
        None,
        "192.168.100.2",
    )


def test_a_virtual_address_outside_the_segment_is_still_caught():
    """読めるようになった以上、判定も効く。"""
    from app.services import policyreview

    text = OFFICIAL_REDUNDANCY.replace("address 192.168.1.250 primary", "address 10.9.9.1 primary")
    titles = [f.title for f in policyreview.review(configparse.parse(text), text)]
    assert any("VRRP グループ 1 の仮想アドレス 10.9.9.1" in title for title in titles), titles


def test_ports_written_as_a_range_are_opened_one_by_one():
    """`interface 1/1/1-1/1/4` は4つの口。1つの変な名前の口にしない。

    開かないと、その4口が台帳にも図にも実在しない名前で1つだけ出る。

    出典: AOS-CX Link Aggregation Guide
    （"To assign a range of interfaces to a LAG:
    switch(config-lag-if)# interface 1/1/1-1/1/4"）。
    """
    text = "\n".join(
        [
            "!Version ArubaOS-CX GL.10.09.0010",
            "hostname cx-01",
            "vlan 10",
            "    name USERS",
            "interface 1/1/1-1/1/4",
            "    no shutdown",
            "    vlan access 10",
            "interface 1/1/24",
            "    no shutdown",
            "    vlan trunk allowed 10",
        ]
    )
    parsed = configparse.parse(text)
    ports = {i.name: i for i in parsed.interfaces}
    assert sorted(ports) == ["1/1/1", "1/1/2", "1/1/24", "1/1/3", "1/1/4"]
    assert all(ports[name].vlan == 10 for name in ("1/1/1", "1/1/2", "1/1/3", "1/1/4"))
    assert all(ports[name].enabled for name in ("1/1/1", "1/1/4"))
    # 範囲の書き方そのままの口は作らない
    assert "1/1/1-1/1/4" not in ports
