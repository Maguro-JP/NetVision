"""Juniper Junos の読み取り。"""

from app.services import configparse


def test_a_subinterface_keeps_its_tag_and_its_address_together():
    """Junos はアドレスも VLAN も unit に付く。分けて持つと、どちらか片方になる。"""
    text = """set system host-name r1
set interfaces ge-0/0/0 unit 0 family inet address 203.0.113.2/30
set interfaces ge-0/0/1 unit 10 vlan-id 10
set interfaces ge-0/0/1 unit 10 family inet address 10.0.10.1/24
set interfaces ge-0/0/1 unit 20 vlan-id 20
set interfaces ge-0/0/1 unit 20 family inet address 10.0.20.1/24
"""
    ports = {item.name: item for item in configparse.parse(text).interfaces}
    # unit 0 は物理の口そのもの。隣接情報もこの名前で来る
    assert ports["ge-0/0/0"].ip_address == "203.0.113.2/30"
    assert ports["ge-0/0/1.10"].vlan == 10
    assert ports["ge-0/0/1.10"].ip_address == "10.0.10.1/24"
    assert ports["ge-0/0/1.20"].vlan == 20


def test_the_peer_as_written_on_the_group_reaches_its_neighbours():
    """Junos は相手の AS をまとまり側に書ける。読まないと外の AS だと気づけない。"""
    text = """set system host-name r1
set routing-options autonomous-system 65001
set protocols bgp group EXT type external
set protocols bgp group EXT peer-as 65002
set protocols bgp group EXT neighbor 203.0.113.1
set protocols bgp group INT neighbor 10.0.0.2 peer-as 65001
"""
    bgp = configparse.parse(text).bgp
    assert bgp.local_as == "65001"
    assert sorted((n.address, n.remote_as) for n in bgp.neighbors) == [
        ("10.0.0.2", "65001"),
        ("203.0.113.1", "65002"),
    ]
