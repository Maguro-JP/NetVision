"""論理構成図（トンネル）。

物理は「ケーブルがどう繋がっているか」、論理は「その上で通信がどう流れるか」。
拠点は物理では支社・センターを経由していても、論理ではセンターと直結している。
このズレが障害の切り分けで効くので、物理とは別の図として出せること。

危ないのは2つ。
1. 相手が特定できないトンネルを黙って捨てる（「トンネルが無い」と読まれる）
2. それらしい機器を当てはめる（張られていない線が図に出る）
"""

from app.models.device import Device, DeviceType
from app.models.tunnel import DeviceTunnel
from app.services import configparse, topology, topology_logical
from tests.conftest import API


def add(db, hostname: str, mgmt_ip: str | None = None) -> int:
    device = Device(
        hostname=hostname,
        vendor="Cisco",
        model="ISR4451-X",
        device_type=DeviceType.ROUTER,
        mgmt_ip=mgmt_ip,
    )
    db.add(device)
    db.commit()
    return device.id


def tunnel(db, device_id: int, name: str, destination: str | None, **kwargs) -> None:
    db.add(DeviceTunnel(device_id=device_id, name=name, destination=destination, **kwargs))
    db.commit()


def test_a_tunnel_becomes_a_line_between_the_two_ends(db_session):
    center = add(db_session, "center-rt-01", "10.1.0.1")
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel0", "10.1.0.1", kind="ipsec", description="センター向け")

    result = topology_logical.build(db_session)
    assert len(result.graph.edges) == 1
    edge = result.graph.edges[0]
    assert {edge.source, edge.target} == {center, station}
    assert edge.source_interface == "Tunnel0"
    assert edge.label == "センター向け"
    assert result.unresolved == []


def test_a_tunnel_to_an_unknown_peer_is_reported_not_dropped(db_session):
    """台帳に無い相手（他社側・クラウド）に張っているのは普通のこと。

    黙って落とすと「トンネルが無い」と読まれるので、必ず件数に出す。
    """
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel1", "198.51.100.9", kind="ipsec")

    result = topology_logical.build(db_session)
    assert result.graph.edges == []
    assert [u.name for u in result.unresolved] == ["Tunnel1"]
    assert result.unresolved[0].destination == "198.51.100.9"


def test_no_peer_is_invented_when_the_destination_is_missing(db_session):
    """宛先が書かれていないトンネルで、相手を勝手に決めないこと。"""
    add(db_session, "center-rt-01", "10.1.0.1")
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel0", None)

    result = topology_logical.build(db_session)
    assert result.graph.edges == []
    assert len(result.unresolved) == 1


def test_only_devices_with_tunnels_are_drawn(db_session):
    """トンネルの無い機器は論理図に出さない。線の無い箱ばかりでは読めない。"""
    center = add(db_session, "center-rt-01", "10.1.0.1")
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    add(db_session, "kobe-rt-01", "10.101.2.1")
    tunnel(db_session, station, "Tunnel0", "10.1.0.1")

    result = topology_logical.build(db_session)
    assert {n.device_id for n in result.graph.nodes} == {center, station}


def test_both_sides_configured_is_still_one_line(db_session):
    """相互に張ってあっても線は1本。2本描くと冗長構成と読み違える。"""
    center = add(db_session, "center-rt-01", "10.1.0.1")
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel0", "10.1.0.1")
    tunnel(db_session, center, "Tunnel0", "10.101.1.1")

    assert len(topology_logical.build(db_session).graph.edges) == 1


def test_layer_logical_goes_through_the_usual_graph(db_session):
    """既存の図の経路（画面・書き出し）がそのまま使えること。"""
    add(db_session, "center-rt-01", "10.1.0.1")
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel0", "10.1.0.1")

    graph = topology.get_graph(db_session, "logical")
    assert graph.layer == "logical"
    assert len(graph.edges) == 1


def test_endpoint_lists_unresolved(client, db_session):
    station = add(db_session, "osaka-rt-01", "10.101.1.1")
    tunnel(db_session, station, "Tunnel1", "198.51.100.9")

    body = client.get(f"{API}/topology/tunnels").json()
    assert body["tunnel_count"] == 0
    assert body["unresolved"][0]["name"] == "Tunnel1"


CISCO = """hostname rt-01
interface Tunnel0
 description センター向け
 tunnel mode ipsec ipv4
 tunnel source GigabitEthernet0/0
 tunnel destination 10.1.0.1
!
"""

JUNOS = """set system host-name mx-01
set interfaces gr-0/0/0 unit 0 tunnel source 198.51.100.1
set interfaces gr-0/0/0 unit 0 tunnel destination 10.1.0.1
"""

FORTIOS = """#config-version=FGT100F-7.2.8:opmode=0
config system global
set hostname "fw-01"
end
config vpn ipsec phase1-interface
    edit "toHQ"
        set interface "wan1"
        set remote-gw 10.1.0.1
    next
end
"""

IX = """device-name ix-rt-01
interface Tunnel0.0
 description センター向け
 tunnel destination 10.1.0.1
!
"""


def test_each_vendor_syntax_is_read():
    """ベンダーを跨いだ瞬間に黙って空になる、が一番起きやすい。"""
    for text, expected in ((CISCO, "Tunnel0"), (JUNOS, "gr-0/0/0.0"), (FORTIOS, "toHQ")):
        parsed = configparse.parse(text)
        assert [t.name for t in parsed.tunnels] == [expected], text[:40]
        assert parsed.tunnels[0].destination == "10.1.0.1"

    parsed = configparse.parse(IX)
    assert parsed.tunnels and parsed.tunnels[0].destination == "10.1.0.1"


def test_junos_ip_ip_tunnel_is_not_read_as_ipsec():
    """Junos の IP-IP トンネルは ip-0/0/0、GRE は gr-0/0/0 に載る。IP-IP を IPsec と読まない。

    出典: Juniper「Chassis Cluster Redundant Ethernet Interfaces」の IP-over-IP Tunneling
    （GRE tunnels use the gr-0/0/0 interface. IP-IP tunnels use the ip-0/0/0 interface.）。
    """
    text = (
        "set interfaces gr-0/0/0 unit 0 tunnel source 10.1.4.1\n"
        "set interfaces gr-0/0/0 unit 0 tunnel destination 10.1.4.2\n"
        "set interfaces ip-0/0/0 unit 0 tunnel source 10.1.4.1\n"
        "set interfaces ip-0/0/0 unit 0 tunnel destination 10.1.4.2\n"
    )
    kinds = {t.name: t.kind for t in configparse.parse(text).tunnels}
    assert kinds == {"gr-0/0/0.0": "gre", "ip-0/0/0.0": "ipip"}


def test_fortios_ipv6_gateways_are_read():
    """FortiOS の IPv6 の IPsec は `set remote-gw6` / `set local-gw6` に書く。宛先を空にしない。

    出典: FortiOS 6.4.7 / 7.0.1 CLI Reference の config vpn ipsec phase1-interface
    （remote-gw6: IPv6 address of the remote gateway's external interface）。
    """
    text = (
        "config vpn ipsec phase1-interface\n"
        '    edit "toHQ6"\n'
        "        set ip-version 6\n"
        '        set interface "wan1"\n'
        "        set local-gw6 2001:db8::1\n"
        "        set remote-gw6 2001:db8::2\n"
        "    next\n"
        "end\n"
    )
    parsed = configparse.parse('config system global\nset hostname "fw-01"\nend\n' + text)
    assert [(t.name, t.source, t.destination) for t in parsed.tunnels] == [
        ("toHQ6", "2001:db8::1", "2001:db8::2")
    ]


def test_cisco_tunnel_lines_follow_the_ip_routing_guide():
    """Cisco IOS XE の GRE と VRF を意識したトンネルの例を、そのまま読む。

    出典: Cisco「IP Routing Configuration Guide, Cisco IOS XE 17.x」の Implementing Tunnels
    （Example: Configuring a GRE IPv4 Tunnel、`interface Tunnel 0` と番号の前に空白）と
    VRF-Aware Tunnels（`vrf forwarding green` が内側、`tunnel vrf red` が外側）。
    """
    text = (
        "hostname rt-01\n"
        "interface Tunnel 0\n"
        " ip address 10.1.1.2 255.255.255.0\n"
        " tunnel source GigabitEthernet 0/0/1\n"
        " tunnel destination 192.168.3.2\n"
        " tunnel mode gre ip\n"
        "!\n"
        "interface Tunnel1\n"
        " no ip address\n"
        " vrf forwarding green\n"
        " tunnel source 10.22.22.22\n"
        " tunnel destination 10.44.44.44\n"
        " tunnel vrf red\n"
        "!\n"
    )
    tunnels = {t.name: t for t in configparse.parse(text).tunnels}
    assert sorted(tunnels) == ["Tunnel0", "Tunnel1"]
    gre = tunnels["Tunnel0"]
    assert (gre.kind, gre.source, gre.destination) == (
        "gre ip",
        "GigabitEthernet 0/0/1",
        "192.168.3.2",
    )
    assert (tunnels["Tunnel1"].destination, tunnels["Tunnel1"].vrf) == ("10.44.44.44", "green")


def test_h3c_tunnel_with_the_mode_on_the_interface_line_is_read():
    """H3C Comware 7 はトンネルの種別を見出しの行に書く（`interface tunnel 0 mode gre`）。

    出典: H3C「GRE over IPv4 Tunnel Configuration Examples」（Comware 7-based routers。
    `interface tunnel 0 mode gre`、`source 1.1.1.1`、`destination 2.2.2.2`）と、
    Tunnel interface commands の interface tunnel
    （`interface tunnel number [ mode { ... | gre [ ipv6 ] | ... } ]`）。
    保存した設定で `interface Tunnel0 mode gre` と出る形は資料で見ていない（未確認）。
    """
    for header in ("interface tunnel 0 mode gre", "interface Tunnel0 mode gre"):
        text = (
            "#\n sysname DeviceA\n#\n"
            f"{header}\n"
            " ip address 10.1.2.1 255.255.255.0\n"
            " source 1.1.1.1\n"
            " destination 2.2.2.2\n"
            "#\nreturn\n"
        )
        tunnels = configparse.parse(text).tunnels
        assert [(t.name, t.kind, t.source, t.destination) for t in tunnels] == [
            ("Tunnel0", "gre", "1.1.1.1", "2.2.2.2")
        ], header
