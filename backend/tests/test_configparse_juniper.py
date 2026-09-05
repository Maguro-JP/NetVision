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


def test_the_hostname_is_read_from_show_version():
    """取り込みで機器と突き合わせるのはホスト名である。

    show version だけを貼られたとき、ここを読めないとどの機器の情報なのか
    決まらず、台帳に結び付かない。
    """
    text = "Hostname: edge-rt-01\nModel: mx204\nJunos: 21.4R3-S4.9\n"
    parsed = configparse.parse(text)
    assert parsed.vendor is configparse.Vendor.JUNIPER
    assert parsed.hostname == "edge-rt-01"
    assert parsed.model == "mx204"
    assert parsed.os_version == "21.4R3-S4.9"


def test_the_configuration_forms_still_give_the_hostname():
    """set 形式と波括弧形式は、これまで通り読める。"""
    assert configparse.parse("set system host-name edge-rt-02\n").hostname == "edge-rt-02"
    braces = "system {\n    host-name edge-rt-03;\n}\ninterfaces {\n    ge-0/0/0 {\n    }\n}\n"
    assert configparse.parse(braces).hostname == "edge-rt-03"
