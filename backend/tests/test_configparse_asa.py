"""Cisco ASA / FTD（ファイアウォール）を読めること。

同じ Cisco でも、スイッチやルーターとは別物として扱う必要がある。ASA は
どちら側が内側かを security-level という数字で決めていて、名前ではなく
その数字が効く。名前だけ見ていると気づけないところなので、そこも見る。
"""

from pathlib import Path

from app.services import configparse, policyreview
from app.services.configparse import asa
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load() -> str:
    return (FIXTURES / "asa-hq-01.txt").read_text()


def parsed():
    return configparse.parse(load())


def titles(text: str) -> list[str]:
    return [f.title for f in policyreview.review(configparse.parse(text), text)]


def test_it_is_detected_as_asa_and_not_as_ios():
    p = parsed()
    assert p.vendor is Vendor.CISCO_ASA
    assert (p.hostname, p.model, p.os_version, p.serial_number) == (
        "fw-hq-01",
        "ASA5516",
        "9.16(4)",
        "JAD12345678",
    )


def test_ports_keep_both_the_name_and_the_number():
    """名前を outside にしても、効くのは数字のほう。両方を持っておく。"""
    ports = {i.name: i for i in parsed().interfaces}
    outside = ports["GigabitEthernet0/0"]
    assert asa.nameif_of(outside) == "outside"
    assert asa.security_level_of(outside) == 0
    assert asa.security_level_of(ports["GigabitEthernet0/1"]) == 100
    # 他のベンダーと同じ形で持つ。並べ方が違うだけの同じものを、比べられる形にそろえる
    assert outside.ip_address == "203.0.113.2/30"


def test_an_access_list_line_is_read_with_its_object_names():
    """アドレスもサービスも名前で書かれる。宛先とポートを取り違えない。"""
    acls = {a.name: a for a in parsed().acls}
    first = acls["OUTSIDE-IN"].entries[0]
    assert (first.action, first.protocol, first.source) == ("permit", "tcp", "any")
    assert first.destination == "WEB-SERVER"
    assert first.destination_port == "サービス WEB-PORTS"
    assert acls["OUTSIDE-IN"].applied_to == ["interface outside 入り"]
    # 最後の1行は落として記録する
    assert acls["OUTSIDE-IN"].entries[1].log is True


def test_both_ways_of_writing_nat_are_read():
    nat = parsed().nat
    assert nat is not None
    kinds = {(r.kind, r.matched, r.translated, r.overload) for r in nat.rules}
    # object の中に書く形（内側から出るときに出口のアドレスへ束ねる）
    assert ("dynamic", "INSIDE-NET", "出口のアドレス（interface）", True) in kinds
    # 外から中の1台へ通す形
    assert ("static", "WEB-SERVER", "203.0.113.10", False) in kinds
    assert nat.outside_interfaces == ["outside"]


def test_routes_are_written_in_the_same_form_as_other_vendors():
    """ASA は口の名前から書く。宛先の形は他ベンダーとそろえる。"""
    routes = {r.destination: r for r in parsed().routes}
    assert routes["0.0.0.0/0"].next_hop == "203.0.113.1"
    assert routes["0.0.0.0/0"].interface == "outside"
    assert routes["10.20.0.0/16"].next_hop == "10.1.0.254"


def test_a_clean_firewall_gets_only_the_note_about_an_explicit_deny():
    """名前で書いた object を「無い」と言わないこと。"""
    found = titles(load())
    assert [t for t in found if "この Config にありません" in t] == []
    assert found == ["ACL INSIDE-OUT には、明示的に落とす行がありません"]


def test_an_outside_port_with_the_inside_number_is_the_heaviest():
    """名前は outside、数字は 100。名前だけ見ていると気づけない。"""
    text = load().replace(
        " nameif outside\n security-level 0", " nameif outside\n security-level 100"
    )
    assert [t for t in titles(text) if "外向きの名前なのに、内側と同じ扱いです" in t]


def test_the_same_level_on_two_ports_is_mentioned():
    text = load().replace(" security-level 50", " security-level 100")
    assert [t for t in titles(text) if "security-level が同じ口が" in t]


def test_an_outside_port_without_an_inbound_acl_is_reported():
    text = load().replace("access-group OUTSIDE-IN in interface outside\n", "")
    assert [t for t in titles(text) if "入る側の制限がありません" in t]


def test_a_switch_config_is_not_mistaken_for_a_firewall():
    """IOS と ASA は見分けが付きにくい。取り違えると読み方ごと変わる。"""
    ios = "hostname sw-01\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n"
    assert configparse.parse(ios).vendor is Vendor.CISCO_IOS


def test_the_thing_being_translated_is_not_the_keyword():
    """`source dynamic 内側 出口` の「内側」を取る。

    dynamic / static は書き方の指定であって、変換するものの名前ではない。
    取り違えると、NAT の表に "dynamic" という名前の対象が並ぶ。
    """
    text = """ASA Version 9.16(4)
hostname fw1
nat (inside,outside) source dynamic INSIDE-NET interface
nat (inside,outside) source static SRV-IN SRV-OUT
"""
    rules = configparse.parse(text).nat.rules
    assert [(r.kind, r.matched) for r in rules] == [
        ("dynamic", "INSIDE-NET"),
        ("static", "SRV-IN"),
    ]
    assert rules[1].translated == "SRV-OUT"
    assert "出口" in rules[0].translated


def test_the_object_form_still_reads_its_destination():
    text = """ASA Version 9.16(4)
hostname fw1
object network INSIDE-NET
 subnet 10.0.0.0 255.255.0.0
 nat (inside,outside) dynamic interface
"""
    rule = configparse.parse(text).nat.rules[0]
    assert rule.matched == "INSIDE-NET"
    assert "出口" in rule.translated


def test_the_show_version_output_is_read_too():
    """設定ファイルの `ASA Version` とは書き方が違う。

    現場で貼られるのは show version のことも多い。読めないと、その機器は
    型番も版数も空になり、弱点の照合から丸ごと漏れる。
    """
    text = "\n".join(
        [
            "Cisco Adaptive Security Appliance Software Version 9.16(4)",
            "Hardware:   ASA5516, 8192 MB RAM",
            "Serial Number: JAD1234ABCD",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.CISCO_ASA
    assert parsed.model == "ASA5516"
    assert parsed.os_version == "9.16(4)"
    assert parsed.serial_number == "JAD1234ABCD"


def test_firepower_threat_defense_is_read_the_same_way():
    text = "\n".join(
        [
            "Cisco Firepower Threat Defense for VMware, Version 7.2.5",
            "Hardware:   FPR-2110, 16384 MB RAM",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.CISCO_ASA
    assert parsed.model == "FPR-2110"
    assert parsed.os_version == "7.2.5"


def test_the_written_description_is_kept_next_to_nameif() -> None:
    # nameif と数字は説明の欄に持たせているが、人が書いた説明を
    # 押しのけてしまうと現場の呼び名が消える
    text = "\n".join(
        [
            "Cisco Adaptive Security Appliance Software Version 9.16(4)",
            "hostname asa1",
            "interface GigabitEthernet0/0",
            " description 本社WAN",
            " nameif outside",
            " security-level 0",
            " ip address 203.0.113.2 255.255.255.252",
        ]
    )
    parsed = configparse.parse(text)
    port = parsed.interfaces[0]
    assert port.description is not None
    assert "本社WAN" in port.description
    assert asa.nameif_of(port) == "outside"
    assert asa.security_level_of(port) == 0


ASA_VPN = """\
ASA Version 9.16(4)
hostname fw-01
interface Ethernet1/1
 nameif outside
 security-level 0
 ip address 198.51.100.2 255.255.255.248
object network LAN
 subnet 192.168.1.0 255.255.255.0
nat (inside,outside) after-auto source dynamic LAN interface
crypto ikev2 policy 10
 encryption aes-256
 integrity sha256
 group 14
crypto ipsec ikev2 ipsec-proposal AES256
 protocol esp encryption aes-256
 protocol esp integrity sha-256
crypto map OUTSIDE_MAP 10 match address VPN_ACL
crypto map OUTSIDE_MAP 10 set peer 203.0.113.1
crypto map OUTSIDE_MAP 10 set ikev2 ipsec-proposal AES256
crypto map OUTSIDE_MAP interface outside
tunnel-group 203.0.113.1 type ipsec-l2l
tunnel-group 203.0.113.1 ipsec-attributes
 ikev2 remote-authentication pre-shared-key *****
failover
failover lan unit primary
ssh 192.168.1.0 255.255.255.0 inside
"""


def test_the_one_line_crypto_map_of_asa_is_read_as_one_entry():
    """ASA は crypto map を1行ずつ書く。IOS の塊として読むと行ごとに別物になる。"""
    crypto = configparse.parse(ASA_VPN).crypto
    assert len(crypto.maps) == 1
    entry = crypto.maps[0]
    assert (entry.name, entry.seq) == ("OUTSIDE_MAP", "10")
    assert entry.peers == ["203.0.113.1"]
    assert entry.transform_sets == ["AES256"]
    assert entry.match_address == "VPN_ACL"
    assert crypto.applied == [("outside", "OUTSIDE_MAP")]
    assert crypto.keyed_peers == ["203.0.113.1"]


def test_the_ikev2_policy_and_proposal_of_asa_are_read():
    crypto = configparse.parse(ASA_VPN).crypto
    policy = crypto.ike_policies[0]
    assert (policy.version, policy.encryption, policy.hash, policy.group) == (
        2,
        "aes-256",
        "sha256",
        "14",
    )
    assert crypto.transform_sets[0].transforms == ["esp-aes-256", "esp-sha-256"]


def test_after_auto_is_an_order_and_not_the_thing_translated():
    rule = configparse.parse(ASA_VPN).nat.rules[0]
    assert (rule.kind, rule.matched, rule.overload) == ("dynamic", "LAN", True)
    assert rule.translated == "出口のアドレス（interface）"


def test_asa_nat_ssh_and_failover_show_up_as_features():
    names = {(f.category, f.name) for f in configparse.parse(ASA_VPN).features}
    assert ("nat", "NAT") in names
    assert ("management", "SSH") in names
    assert ("high_availability", "フェイルオーバー") in names
