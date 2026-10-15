"""MikroTik RouterOS を読む。

`/` で始まる区画に入り、`名前=値` で書く。IOS の目で読むと何も取れない。
"""

from app.services import configparse
from app.services.configparse.base import Vendor

EXPORT = """# sep/07/2026 10:00:00 by RouterOS 7.14.2
# model = CCR2004
/interface bridge
add name=bridge1 vlan-filtering=yes
/interface vlan
add interface=bridge1 name=vlan10 vlan-id=10
add interface=bridge1 name=vlan30 vlan-id=30
/interface ethernet
set [ find default-name=ether1 ] comment="to core" name=ether1
set [ find default-name=ether5 ] disabled=yes name=ether5
/interface bridge port
add bridge=bridge1 interface=ether1 pvid=999
/ip address
add address=10.0.30.1/24 interface=vlan30 \\
    network=10.0.30.0
add address=10.0.0.1/24 interface=bridge1
/ip route
add dst-address=0.0.0.0/0 gateway=10.0.0.254 distance=1
/system identity
set name=rt-mt-01
"""


def _parsed():
    return configparse.parse(EXPORT)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_it_is_recognised_with_its_name_and_version() -> None:
    parsed = _parsed()
    assert parsed.vendor is Vendor.ROUTEROS
    assert parsed.hostname == "rt-mt-01"
    assert parsed.os_version == "7.14.2"
    assert parsed.model == "CCR2004"


def test_the_vlans_are_read_from_the_ports_that_carry_them() -> None:
    """RouterOS は VLAN の定義と、その口を1行で兼ねて書く。"""
    assert _parsed().vlans == {10: "vlan10", 30: "vlan30"}


def test_the_address_written_in_another_section_reaches_the_port() -> None:
    """住所は口とは別の区画に書く。付け直さないと、どこにも出ない。"""
    assert _port("vlan30").ip_address == "10.0.30.1/24"
    assert _port("bridge1").ip_address == "10.0.0.1/24"


def test_a_line_folded_with_a_backslash_is_read_whole() -> None:
    """繋がないと後ろ半分が落ちる。`interface=` が落ちれば住所は宙に浮く。"""
    from app.services.configparse.routeros import _joined

    joined = _joined(EXPORT)
    assert any("address=10.0.30.1/24" in line and "network=" in line for line in joined)


def test_the_untagged_vlan_on_a_bridge_port_is_read() -> None:
    assert _port("ether1").native_vlan == 999


def test_a_pvid_on_a_bridge_that_does_not_sort_by_vlan_is_not_taken_as_native() -> None:
    """効いていない値を根拠に、両端の食い違いを言うことになる。

    RouterOS の pvid が効くのは、その橋が `vlan-filtering=yes` のときだけ。
    出典: MikroTik RouterOS マニュアル「Bridging and Switching」の pvid
    （「This property only has an effect when vlan-filtering is set to yes.」）。
    """
    text = (
        "/interface bridge\nadd name=bridge1\n"
        "/interface bridge port\nadd bridge=bridge1 interface=ether1 pvid=999\n"
        "/system identity\nset name=rt-mt-01\n"
    )
    port = next(p for p in configparse.parse(text).interfaces if p.name == "ether1")
    assert port.native_vlan is None


def test_a_port_that_is_only_stopped_is_not_counted_as_live() -> None:
    """RouterOS は消さずに止める。止めてある口を生きていると数えない。"""
    assert _port("ether5").enabled is False
    assert _port("ether1").enabled is True


def test_the_comment_is_kept_as_the_description() -> None:
    assert _port("ether1").description == "to core"


def test_the_route_is_read_with_its_distance() -> None:
    route = _parsed().routes[0]
    assert route.destination == "0.0.0.0/0"
    assert route.next_hop == "10.0.0.254"
    assert route.distance == 1


def test_no_commands_are_offered_for_a_machine_we_cannot_write() -> None:
    """行を no で打ち消す形が無い。IOS の規則で出すと入らない形になる。"""
    from app.services.configdiff import build

    diff = build(
        EXPORT,
        EXPORT + "/ip address\nadd address=10.0.40.1/24 interface=vlan40\n",
        vendor=Vendor.ROUTEROS,
    )
    assert not diff.script.strip()
    assert diff.warnings


def test_a_route_without_dst_address_is_the_default_route():
    """MikroTik RouterOS マニュアル「IP Routing」: 既定経路は dst-address を省いて書ける
    （作例 `/ip route add gateway=172.16.1.1`）。以前は読み飛ばしていた。"""
    from app.services.configparse import routeros

    parsed = routeros.parse(
        "# by RouterOS 7.15\n/ip route\nadd gateway=172.16.1.1\n"
        "add dst-address=10.0.0.0/8 gateway=10.1.1.1 distance=5\n"
    )
    routes = {(r.destination, r.next_hop, r.distance) for r in parsed.routes}

    assert ("0.0.0.0/0", "172.16.1.1", None) in routes
    assert ("10.0.0.0/8", "10.1.1.1", 5) in routes


def test_tagged_vlans_on_a_bridge_are_read_as_trunk_vlans() -> None:
    """`/interface bridge vlan` の tagged に並んだ口は、その vlan-ids をトランクで通す。

    出典: MikroTik RouterOS マニュアル（help.mikrotik.com）「Bridging and Switching」の
    Bridge VLAN Filtering（tagged は「comma-separated values. e.g. tagged=ether1,ether2」、
    vlan-ids は「the VLAN ID range as well as comma-separated values. e.g. vlan-ids=100-115,120」、
    pvid は「only has an effect when vlan-filtering is set to yes」）。
    vlan-filtering の無い橋の行は効かないので読まない。
    """
    text = "\n".join(
        [
            "/interface bridge",
            "add name=bridge1 vlan-filtering=yes",
            "add name=bridge2",
            "/interface bridge port",
            "add bridge=bridge1 interface=ether1",
            "add bridge=bridge1 interface=ether2 pvid=20",
            "add bridge=bridge2 interface=ether3",
            "/interface bridge vlan",
            "add bridge=bridge1 tagged=bridge1,ether1 vlan-ids=10,30-32",
            "add bridge=bridge1 tagged=ether1 untagged=ether2 vlan-ids=20",
            "add bridge=bridge2 tagged=ether3 vlan-ids=40",
            "/system identity",
            "set name=rt-mt-02",
        ]
    )
    ports = {p.name: p for p in configparse.parse(text).interfaces}
    assert sorted(ports["ether1"].trunk_vlans) == [10, 20, 30, 31, 32]
    assert ports["ether2"].trunk_vlans == []
    assert ports["ether2"].native_vlan == 20
    assert ports["ether3"].trunk_vlans == []
