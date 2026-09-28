"""FortiGate（FortiOS）の読み取り。"""

from app.services import configparse


def test_the_version_is_read_even_when_the_header_is_cut_short():
    """版が読めないと、その機器は弱点の照合から漏れる。"""
    for header, model, version in (
        ("#config-version=FGT100F-7.2.8-FW-build1639-230929:opmode=0:vdom=0", "FGT100F", "7.2.8"),
        ("#config-version=FGT100F-7.2.8", "FGT100F", "7.2.8"),
        ("#config-version=FGT60F-7.4-FW-build2573-240201:opmode=0", "FGT60F", "7.4"),
        ("#config-version=FWF60E-6.4.14-FW-build2093-230314:opmode=0", "FWF60E", "6.4.14"),
    ):
        parsed = configparse.parse(f"{header}\nconfig system global\n set hostname fw1\nend\n")
        assert (parsed.model, parsed.os_version) == (model, version), header


def test_a_pasted_section_is_still_read_as_fortigate() -> None:
    """設定の一部だけを貼られることは多い。先頭の版数行が無くても読む。"""
    text = "\n".join(
        [
            "config system interface",
            '    edit "port1"',
            "        set ip 203.0.113.2 255.255.255.248",
            "    next",
            "end",
            "config router static",
            "    edit 1",
            "        set dst 10.20.0.0 255.255.0.0",
            "        set gateway 203.0.113.1",
            '        set device "port1"',
            "    next",
            "end",
        ]
    )
    parsed = configparse.parse(text)
    assert parsed.vendor is configparse.Vendor.FORTINET
    assert [i.ip_address for i in parsed.interfaces] == ["203.0.113.2/29"]
    assert [r.destination for r in parsed.routes] == ["10.20.0.0/16"]


def test_plain_prose_is_not_mistaken_for_fortigate() -> None:
    """end や next という語が出てくるだけの文章を、設定と取り違えない。"""
    text = "\n".join(["config change memo", "next", "end"])
    assert configparse.parse(text).vendor is not configparse.Vendor.FORTINET


def test_vlan_interfaces_count_as_the_vlans_on_the_device() -> None:
    """FortiOS には VLAN を並べる場所が無い。

    `set vlanid` を持つ口が、その VLAN そのものである。拾わないと、VLAN
    台帳との突き合わせで FortiGate だけ「どの VLAN も持っていない」ことに
    なる。
    """
    text = "\n".join(
        [
            "config system interface",
            '    edit "vlan10"',
            "        set vlanid 10",
            '        set description "社員端末"',
            "        set ip 10.0.10.1 255.255.255.0",
            "    next",
            '    edit "vlan20"',
            "        set vlanid 20",
            "        set ip 10.0.20.1 255.255.255.0",
            "    next",
            '    edit "port1"',
            "        set ip 203.0.113.2 255.255.255.248",
            "    next",
            "end",
        ]
    )
    parsed = configparse.parse(text)
    assert set(parsed.vlans) == {10, 20}
    assert parsed.vlans[10] == "社員端末"
    # 名前が書いていないものに、口の名前を当てはめない。
    # 当てはめると、台帳と突き合わせたときに「名前が違います」の誤りになる
    assert parsed.vlans[20] == ""


OFFICIAL_POLICY = """\
#config-version=FGT60F-7.2.8-FW-build1639-230929:opmode=0
config system global
    set hostname "fw-01"
end
config firewall policy
    edit 1
        set name "allow web access"
        set srcintf "lan1" "lan2"
        set dstintf "wan1"
        set srcaddr "all"
        set dstaddr "srv-web" "srv-api"
        set action accept
        set schedule "always"
        set service "HTTP" "HTTPS"
        set nat enable
    next
    edit 2
        set name "deny-policy-1"
        set srcintf "wan1"
        set dstintf "lan1"
        set srcaddr "src-hosts-to-deny-access"
        set dstaddr "all"
        set action "deny"
        set schedule "always"
        set service "ALL"
        set match-vip enable
    next
end
"""


def _official_entries():
    acl = next(a for a in configparse.parse(OFFICIAL_POLICY).acls if a.entries)
    return {entry.seq: entry for entry in acl.entries}


def test_the_fields_that_take_several_values_do_not_lose_the_rest():
    """出典: FortiOS CLI リファレンス `config firewall policy`。

    `set srcintf <name1>, <name2>, ...` も `set service <name1>, <name2>, ...` も
    複数書ける。1つ目だけを読むと、2つの口から入る許可が片方だけに見え、
    2つのサービスを開けているのに1つだけに見える。
    """
    first = _official_entries()["1"]
    assert first.source == "lan1 ほか1件:any"
    assert first.destination == "wan1:srv-web ほか1件"
    assert first.destination_port == "サービス HTTP ほか1件"
    # 原文にも全部の値を残す（あとで人が確かめられるように）
    assert "service HTTP HTTPS" in first.raw


def test_a_name_with_spaces_is_kept_whole():
    """`set name {string}` は引用符の中に空白を書ける。切ると別の名前になる。"""
    assert _official_entries()["1"].remark == "allow web access"


def test_the_action_is_read_whether_or_not_it_is_quoted():
    """管理ガイド「Firewall policy」の例は `set action "deny"` と引用符付き。"""
    assert _official_entries()["2"].action == "deny"
    # `service "ALL"` は全部通る意味なので、絞りとしては持たない
    assert _official_entries()["2"].destination_port is None


def test_the_lldp_table_of_a_managed_switch_gives_the_links():
    """FortiGate 配下の FortiSwitch の LLDP 表から隣を読む。

    繋がっていない口は Device-name が `-` で出る。名前ではないので作らない。

    出典: Fortinet Community KB 227554「How to display LLDP, trunk and STP
    information of a managed FortiSwitch from FortiGate CLI」
    （メーカーのナレッジベース。文書ライブラリ側にはこの表の記載が無い）。
    """
    text = "\n".join(
        [
            "config system global",
            '    set hostname "fw-01"',
            "end",
            "",
            "# diagnose switch-controller switch-info lldp neighbors-summary S108EPXXXXXXXXXX",
            "Portname    Status   Device-name                 TTL   Capability  MED-type  Port-ID",
            "__________  _______  __________________________  ____  __________  ________  _______",
            "port1       Down     -                           -     -           -         -",
            "port4       Up       core-sw-01                  120   BR          -         wan1",
            "",
        ]
    )
    parsed = configparse.parse(text)
    found = {(n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors}
    assert ("port4", "core-sw-01", "wan1") in found
    assert not any(n.local_interface == "port1" for n in parsed.neighbors)


#: 口の中の入れ子。FortiOS CLI Reference「config system interface」（7.0.1・7.6.6）と
#: Administration Guide「IPv6 quick start」の書き方
NESTED_INTERFACES = """#config-version=FGT60F-7.2.8-FW-build1639-230929:opmode=0
config system interface
    edit "port1"
        set vdom "root"
        set ip 192.168.1.99 255.255.255.0
        config ipv6
            set ip6-address 2001:db8::1/64
        end
        set secondary-IP enable
        config secondaryip
            edit 1
                set ip 192.168.2.99 255.255.255.0
            next
        end
        set role lan
    next
    edit "port2"
        set ip 10.0.0.1 255.255.255.0
    next
    edit "vlan10"
        set ip 10.10.0.1 255.255.255.0
        set interface "port1"
        set vlanid 10
    next
end
"""


def _nested_ports():
    # 名前の大文字小文字は、別の処理で揃えられる（vlan10 → Vlan10）。ここでは見ない
    return {i.name.lower(): i for i in configparse.parse(NESTED_INTERFACES).interfaces}


def test_the_ports_after_a_nested_block_are_still_read():
    """入れ子の `end` で読み終えると、後ろの口が全部台帳から消える。"""
    ports = _nested_ports()
    assert ports["port2"].ip_address == "10.0.0.1/24"
    assert ports["vlan10"].vlan == 10


def test_a_secondary_address_does_not_overwrite_the_first():
    ports = _nested_ports()
    assert ports["port1"].ip_address == "192.168.1.99/24"
    assert "role lan" in (ports["port1"].description or "")


def test_an_entry_inside_a_nested_block_is_not_a_port():
    """`config secondaryip` の `edit 1` を口にすると、`1` という偽の口ができる。"""
    assert set(_nested_ports()) == {"port1", "port2", "vlan10"}
