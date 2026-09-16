"""Juniper Junos の読み取り。"""

from app.services import configparse
from app.services.configparse import Vendor


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


def test_unit_description_is_read() -> None:
    # Junos は unit 側に説明を書くことがあり、物理側だけ見ると説明が
    # 無いものとして扱われてしまう
    text = "\n".join(
        [
            "set system host-name r1",
            "set interfaces ge-0/0/0 unit 0 description WAN",
            "set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30",
            'set interfaces ge-0/0/1 description "to core"',
            'set interfaces ge-0/0/2 unit 10 description "sub"',
        ]
    )
    parsed = configparse.parse(text)
    found = {item.name: item.description for item in parsed.interfaces}
    assert found["ge-0/0/0"] == "WAN"
    assert found["ge-0/0/1"] == "to core"
    assert found["ge-0/0/2.10"] == "sub"


OFFICIAL_POLICIES = """\
set system host-name srx-01
set security zones security-zone trust address-book address NW1 192.168.1.0/24
set security zones security-zone trust address-book address NW2 192.168.2.0/24
set security policies from-zone trust to-zone untrust policy trust-to-untrust \
match source-address any
set security policies from-zone trust to-zone untrust policy trust-to-untrust \
match destination-address any
set security policies from-zone trust to-zone untrust policy trust-to-untrust \
match application junos-http
set security policies from-zone trust to-zone untrust policy trust-to-untrust \
match application junos-https
set security policies from-zone trust to-zone untrust policy trust-to-untrust \
match application junos-ping
set security policies from-zone trust to-zone untrust policy trust-to-untrust then permit
set security policies from-zone untrust to-zone trust policy pol_1 \
match source-address [ NW1 NW2 ] destination-address NW1 application [ junos-http junos-https ]
set security policies from-zone untrust to-zone trust policy pol_1 then deny
set security policies from-zone trust to-zone dmz policy pol_2 \
match destination-address srv1 srv2
set security policies from-zone trust to-zone dmz policy pol_2 then permit
"""


def _official_policies():
    parsed = configparse.parse(OFFICIAL_POLICIES)
    acl = next(a for a in parsed.acls if a.kind == "policy")
    return {entry.remark or entry.seq: entry for entry in acl.entries}, acl


def test_an_application_written_on_several_lines_is_not_overwritten():
    """出典: Juniper のブランチ SRX 導入ガイドの設定例。

    `match application junos-http` と `junos-https` と `junos-ping` を
    行ごとに分けて書く。最後の行で上書きすると、開けている口が資料から消える。
    """
    _, acl = _official_policies()
    first = acl.entries[0]
    assert first.destination_port == "アプリ junos-http ほか2件"
    assert first.action == "permit"


def test_values_in_square_brackets_are_read_as_the_names_they_hold():
    """出典: Junos の設定例（1行に項目を並べ、値を角括弧でまとめる形）
    `match source-address [ n_1 n_2 ] destination-address as_1
    application [ junos-http junos-https ]`。

    1語しか読まないと、角括弧そのものをアドレスの名前として読んでしまう。
    1行に並んだ他の項目（destination-address / application）も読み落とす。
    """
    _, acl = _official_policies()
    second = acl.entries[1]
    assert second.source == "untrust:NW1 ほか1件"
    assert second.destination == "trust:NW1"
    assert second.destination_port == "アプリ junos-http ほか1件"
    # アドレス帳の中身は、1つに絞れているときだけ解決する
    assert second.destination_value == "trust:192.168.1.0/24"


def test_two_values_on_one_line_without_brackets_are_both_counted():
    _, acl = _official_policies()
    assert acl.entries[2].destination == "dmz:srv1 ほか1件"


BRACE = """system {
    host-name ex-01;
}
interfaces {
    ge-0/0/0 {
        native-vlan-id 99;
        unit 0 {
            family ethernet-switching {
                interface-mode trunk;
                vlan {
                    members [ USERS VOICE ];
                }
            }
        }
    }
    ge-0/0/1 {
        description "PC";
        unit 0 {
            family ethernet-switching {
                interface-mode access;
                vlan {
                    members USERS;
                }
            }
        }
    }
    irb {
        unit 10 {
            family inet {
                address 10.0.10.1/24;
            }
        }
    }
}
vlans {
    USERS {
        vlan-id 10;
        l3-interface irb.10;
    }
    VOICE {
        vlan-id 20;
    }
}
"""


def test_the_brace_form_is_read_like_the_set_form():
    """囲みで書いた設定（`show configuration` の既定の形）も読む。

    以前は `set` の形しか読めず、囲みの形は VyOS と取り違えたうえ、
    ポートも VLAN も1つも読めていなかった。

    出典: Juniper のサポート記事 [EX/QFX] Example - Configuring VLAN
    translation by using ELS on Junos OS の `show configuration interfaces`
    出力例（ge-0/0/0 { unit 0 { family ethernet-switching { interface-mode
    trunk; vlan { members [ 10 220 ]; } } } }）、Juniper「Interfaces User
    Guide for Switches」
    """
    parsed = configparse.parse(BRACE)
    assert parsed.vendor is Vendor.JUNIPER
    assert parsed.hostname == "ex-01"
    assert sorted(parsed.vlans) == [10, 20]
    ports = {port.name: port for port in parsed.interfaces}
    trunk = ports["ge-0/0/0"]
    assert (trunk.mode, trunk.native_vlan, sorted(trunk.trunk_vlans)) == ("trunk", 99, [10, 20])
    access = ports["ge-0/0/1"]
    assert (access.mode, access.vlan, access.description) == ("access", 10, "PC")
    assert ports["irb.10"].ip_address == "10.0.10.1/24"


def test_the_brace_form_also_feeds_routing_and_security():
    """囲みの形でも、経路・OSPF・BGP・許否まで同じように読む。

    入口で `set` の形にそろえているので、読み手を二重に持たずに済む。
    """
    parsed = configparse.parse(
        "system {\n"
        "    host-name srx-01;\n"
        "}\n"
        "routing-options {\n"
        "    static {\n"
        "        route 0.0.0.0/0 next-hop 203.0.113.1;\n"
        "    }\n"
        "}\n"
        "protocols {\n"
        "    ospf {\n"
        "        area 0.0.0.0 {\n"
        "            interface ge-0/0/0.0;\n"
        "        }\n"
        "    }\n"
        "    bgp {\n"
        "        group EDGE {\n"
        "            peer-as 65001;\n"
        "            neighbor 203.0.113.1;\n"
        "        }\n"
        "    }\n"
        "}\n"
        "security {\n"
        "    policies {\n"
        "        from-zone trust to-zone untrust {\n"
        "            policy allow-web {\n"
        "                match {\n"
        "                    source-address any;\n"
        "                    destination-address any;\n"
        "                    application [ junos-http junos-https ];\n"
        "                }\n"
        "                then {\n"
        "                    permit;\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    assert [(route.destination, route.next_hop) for route in parsed.routes] == [
        ("0.0.0.0/0", "203.0.113.1")
    ]
    assert parsed.ospf is not None and parsed.ospf.areas[0].area == "0.0.0.0"
    assert parsed.bgp is not None and parsed.bgp.neighbors[0].remote_as == "65001"
    assert any(acl.entries for acl in parsed.acls)
