"""NEC IX の書き方を、公式のコマンドリファレンスの入力形式どおりに読むこと。

出典: UNIVERGE IX-R/IX-V シリーズ コマンドリファレンスマニュアル 1.5a
（necplatforms.co.jp、HTML 版）。節番号は本文中に書く。
IX2000/IX3000 のコマンドリファレンス（CRM-ver10.11、PDF）はこの環境から
読めていない。IX-R/IX-V のマニュアルは IX2000/IX3000 との差分を注記する形で、
ここで使う行にその注記は無い。
"""

from app.services import configparse
from app.services.configparse import Vendor

CONFIG = """\
device-name ix-01
ip route default GigaEthernet0.1
ip route 192.168.2.0/24 192.168.1.254 metric 2
ip route 192.168.3.0/24 Tunnel0.0 distance 5
ip route 192.168.4.0/24 Tunnel0.0 connected
ip route vrf CUST 10.9.0.0/16 10.9.9.1
ip dhcp profile aaa
  assignable-range 192.168.5.10 192.168.5.100
  default-gateway 192.168.5.1
  dns-server 192.168.5.1 8.8.8.8
  lease-time 86400
ip dhcp excluded-address 192.168.5.10 192.168.5.20
ip dhcp enable
ip router ospf 100
  router-id 10.255.0.1
  network 192.168.0.0/16 area 0
  network GigaEthernet0.0 area 1
  passive-interface GigaEthernet1.0
  redistribute connected metric 10
ip access-list list1 permit ip src 192.168.1.0/24 dest any
interface GigaEthernet0.0
  description WAN
  ip address dhcp receive-default
  ip napt enable
  ip napt inside list list1
  ip napt static 192.168.0.1 tcp 1000
  ip napt service abc 192.168.1.100 none tcp any
  no shutdown
interface GigaEthernet0.1
  ip address ipcp
  no shutdown
interface GigaEthernet1.0
  ip address 192.168.1.254/24
  ip address 192.168.1.80/24 secondary
  ip dhcp binding aaa
  vrrp enable
  vrrp 1 ip 172.16.0.254
  vrrp 1 priority 254
  no shutdown
interface GigaEthernet1.1
  encapsulation dot1q 10 tpid 9100
  ip address 192.168.10.1/24
  ip dhcp-relay enable
  ip dhcp-relay server 192.168.1.254
  no shutdown
interface Tunnel0.0
  tunnel mode ipsec
  tunnel source 192.168.1.1
  tunnel destination fqdn domain.co.jp
  ip unnumbered GigaEthernet1.0
  no shutdown
"""


def _parsed():
    parsed = configparse.parse(CONFIG)
    assert parsed.vendor is Vendor.NEC_IX
    return parsed


def test_the_address_source_is_kept_when_the_address_is_not_written():
    """9.1.1 `ip address dhcp …` / `ip address ipcp`、9.1.21 `ip unnumbered`。"""
    ports = {p.name: p for p in _parsed().interfaces}
    assert ports["GigaEthernet0.0"].address_source == "dhcp"
    assert ports["GigaEthernet0.1"].address_source == "negotiated"
    assert ports["Tunnel0.0"].address_source == "unnumbered GigaEthernet1.0"
    # 9.1.1 `[ secondary ]` は2つ目として持つ
    assert ports["GigaEthernet1.0"].ip_address == "192.168.1.254/24"
    assert ports["GigaEthernet1.0"].extra_addresses == ["192.168.1.80/24"]


def test_static_routes_read_the_keywords_and_not_their_values_as_ports():
    """9.1.15 `[ connected ] [ metric METRIC ] [ distance DISTANCE ] [ tag TAG ]`。"""
    routes = {(r.vrf, r.destination): r for r in _parsed().routes}
    assert routes[(None, "0.0.0.0/0")].interface == "GigaEthernet0.1"
    metric = routes[(None, "192.168.2.0/24")]
    assert (metric.next_hop, metric.interface, metric.distance) == ("192.168.1.254", None, None)
    distance = routes[(None, "192.168.3.0/24")]
    assert (distance.interface, distance.distance) == ("Tunnel0.0", 5)
    assert routes[(None, "192.168.4.0/24")].interface == "Tunnel0.0"
    assert routes[("CUST", "10.9.0.0/16")].next_hop == "10.9.9.1"


def test_ospf_is_read_from_the_ip_router_ospf_block():
    """11.2.21 `ip router ospf PID`、11.2.22 `network { A.B.C.D/<0-32> | INTERFACE } area AID`。"""
    ospf = _parsed().ospf
    assert (ospf.process_id, ospf.router_id) == ("100", "10.255.0.1")
    areas = {a.area: a.networks for a in ospf.areas}
    assert areas == {"0": ["192.168.0.0/16"], "1": ["interface GigaEthernet0.0"]}
    assert ospf.passive_interfaces == ["GigaEthernet1.0"]
    assert ospf.redistributes == ["connected"]


def test_napt_reads_the_inside_list_the_static_port_and_the_service():
    """9.4.9 `ip napt inside list`、9.4.11 `ip napt static`、9.4.10 `ip napt service`。"""
    rules = {(r.kind, r.matched): r for r in _parsed().nat.rules}
    dynamic = rules[("dynamic", "list1")]
    assert (dynamic.matched_is_name, dynamic.translated, dynamic.overload) == (
        True,
        "GigaEthernet0.0",
        True,
    )
    assert rules[("static-port", "192.168.0.1")].protocol == "tcp"
    service = rules[("static-port", "192.168.1.100")]
    assert (service.protocol, service.translated) == ("tcp", "GigaEthernet0.0")


def test_dhcp_reads_what_it_keeps_back_and_where_it_relays():
    """9.5.10 `ip dhcp excluded-address`、9.5.15 `ip dhcp-relay server`。"""
    dhcp = _parsed().dhcp
    assert dhcp.excluded == [("192.168.5.10", "192.168.5.20")]
    assert dhcp.relays == [("GigaEthernet1.1", "192.168.1.254")]
    assert dhcp.pools[0].range_start == "192.168.5.10"


def test_vrrp_and_the_tunnel_destination_by_name():
    """16.1.3 `vrrp VR-ID ip VR-ADDR`、16.1.6 `vrrp VR-ID priority`、
    12.1 `tunnel destination fqdn 名前`。
    """
    parsed = _parsed()
    entry = parsed.redundancy[0]
    assert (entry.interface, entry.group, entry.virtual_ip, entry.priority) == (
        "GigaEthernet1.0",
        "1",
        "172.16.0.254",
        254,
    )
    tunnel = next(t for t in parsed.tunnels if t.name == "Tunnel0.0")
    assert (tunnel.kind, tunnel.destination) == ("ipsec", "domain.co.jp")


# --- QoS -------------------------------------------------------------------
#
# 別の設定にしてあるのは、上の設定が「読めるか」を見るためのもので、
# 有効化の書き忘れを混ぜると読み分けが見えなくなるため。

QOS_CONFIG = """\
device-name ix-qos
ip access-list pq-list permit ip src 192.168.1.1/32 dest any
class-map match-any class-pq
  match ip access-list pq-list high
  match local-generate-packet medium
  match any low
class-map match-all class-cbq
  match ip access-list pq-list normal
policy-map outmap
  class class-pq
    bandwidth percent 70
  class class-cbq
    shape 5000000
  class class-default
policy-map colour
  class class-pq
    set ip dscp 46
interface GigaEthernet0.0
  ip address 192.168.1.254/24
  service-policy output outmap
  no shutdown
interface GigaEthernet1.0
  ip address 192.168.2.254/24
  service-policy enable
  service-policy output outmap
  no shutdown
interface GigaEthernet2.0
  ip address 192.168.3.254/24
  service-policy output colour
  no shutdown
"""


def _qos_parsed():
    parsed = configparse.parse(QOS_CONFIG)
    assert parsed.vendor is Vendor.NEC_IX
    return parsed


def test_qos_reads_the_classes_the_policies_and_where_they_are_attached():
    """12. QoS編 ポリシーマップ `policy-map POLICYMAP-NAME` / `class CLASS-NAME`。

    match の行には条件ごとの優先度（high / medium / normal / low）が付く。
    出典: UNIVERGE IX シリーズ 障害切り分けガイドライン 2章（match-any の例）。
    """
    qos = _qos_parsed().qos
    classes = {entry.name: entry for entry in qos.classes}
    assert classes["class-pq"].match_type == "match-any"
    assert classes["class-pq"].matches == [
        "match ip access-list pq-list high",
        "match local-generate-packet medium",
        "match any low",
    ]
    assert classes["class-cbq"].match_type == "match-all"
    outmap = next(entry for entry in qos.policies if entry.name == "outmap")
    assert [entry.class_name for entry in outmap.classes] == [
        "class-pq",
        "class-cbq",
        "class-default",
    ]
    assert outmap.classes[0].actions == ["bandwidth percent 70"]
    assert ("GigaEthernet0.0", "output", "outmap") in qos.applied


def test_qos_keeps_which_ports_were_enabled():
    """`service-policy enable` を書いたポートを控える。

    出典: UNIVERGE IX シリーズ 障害切り分けガイドライン
    「QoS で使用するコマンド / 設定を忘れやすいコマンド」。
    """
    qos = _qos_parsed().qos
    assert qos.needs_enable is True
    assert qos.enabled_on == ["GigaEthernet1.0"]


def test_qos_says_when_the_policy_is_attached_but_not_enabled():
    """当てただけで有効にしていないポートを指摘する。色を付けるだけなら言わない。"""
    from app.services import policyreview

    titles = [note.title for note in policyreview.review(_qos_parsed())]
    assert any("GigaEthernet0.0" in title and "有効にしていない" in title for title in titles)
    # 有効にしてあるポートと、マーキングだけのポートは言わない
    assert not any("GigaEthernet1.0" in title for title in titles)
    assert not any("GigaEthernet2.0" in title and "有効" in title for title in titles)


def test_ip_mtu_is_kept_apart_from_the_size_of_the_port_itself():
    """IX の `ip mtu` は IPv4 だけに掛かる。口そのものの大きさではない。

    「IPv4においてインタフェースのMTUより小さい値をMTUとして使用するコマンド」
    出典: IX-R/IX-V コマンドリファレンス「MTU 変更」（ip mtu MTU）
    """
    text = "\n".join(
        [
            "hostname ix-rt-01",
            "!",
            "interface GigaEthernet0.0",
            "  ip address 192.168.0.254/24",
            "  ip mtu 1000",
            "  no shutdown",
            "!",
        ]
    )
    parsed = configparse.parse(text)
    port = next(i for i in parsed.interfaces if i.name == "GigaEthernet0.0")
    assert port.ip_mtu == 1000
    assert port.mtu is None
