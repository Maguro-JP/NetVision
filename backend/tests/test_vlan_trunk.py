"""アクセス / ネイティブ / トランクを取り違えないこと。

VLAN の事故はほぼ全部この3つの混同から出る。
「アクセスのつもりがトランク」「片側だけネイティブが違う」「許可漏れ」。
図や一覧で区別できないと、そもそも気づけない。
"""

from app.services import configparse
from app.services.configparse import vlanlist

CISCO = """
hostname acc-sw-01
!
Model Number: C9200-48P
!
vlan 10
 name USERS
!
vlan 20
 name SERVERS
!
vlan 30
 name MGMT
!
interface GigabitEthernet1/0/1
 description uplink-to-core
 switchport mode trunk
 switchport trunk native vlan 999
 switchport trunk allowed vlan 10,20
 switchport trunk allowed vlan add 30,40-42
!
interface GigabitEthernet1/0/2
 description client
 switchport mode access
 switchport access vlan 10
!
interface GigabitEthernet1/0/3
 description uplink-no-filter
 switchport mode trunk
!
interface GigabitEthernet1/0/4
 description all-but-management
 switchport mode trunk
 switchport trunk allowed vlan except 30
!
"""


def parse_cisco():
    parsed = configparse.parse(CISCO)
    return {i.key: i for i in parsed.interfaces}, parsed


def test_trunk_allowed_vlans_accumulate_across_add_lines():
    """`add` は複数行に分かれる。足し込まないと途中までしか読めない。"""
    ports, _ = parse_cisco()
    uplink = ports["gigabitethernet1/0/1"]
    assert uplink.mode == "trunk"
    assert uplink.trunk_vlans == [10, 20, 30, 40, 41, 42]


def test_native_vlan_is_kept_separate_from_access_vlan():
    """ネイティブとアクセスは別物。混ぜると片側だけの食い違いが見えなくなる。"""
    ports, _ = parse_cisco()
    assert ports["gigabitethernet1/0/1"].native_vlan == 999
    assert ports["gigabitethernet1/0/1"].vlan is None

    access = ports["gigabitethernet1/0/2"]
    assert access.vlan == 10
    assert access.native_vlan is None
    assert access.trunk_vlans == []


def test_trunk_without_allowed_list_carries_everything():
    """許可 VLAN を書かないトランクは全部通す。書き忘れを「何も通らない」と読むと逆。"""
    ports, _ = parse_cisco()
    bare = ports["gigabitethernet1/0/3"]
    assert bare.trunk_all is True
    assert bare.carried_vlans({10, 20, 30}) == {10, 20, 30}


def test_except_removes_only_the_named_vlans():
    ports, _ = parse_cisco()
    port = ports["gigabitethernet1/0/4"]
    assert port.trunk_all is True
    assert port.carried_vlans({10, 20, 30}) == {10, 20}


NECQX = """
 version 5.20.99
NEC QX-S5528P
#
 sysname qx-sw-02
#
vlan 10
 name USERS
#
vlan 20
 name SERVERS
#
interface GigabitEthernet1/0/1
 port link-type trunk
 port trunk permit vlan 10 20 to 22
 port trunk pvid vlan 4094
#
interface GigabitEthernet1/0/2
 port link-type hybrid
 port hybrid vlan 10 20 tagged
 port hybrid pvid vlan 10
#
"""


def test_necqx_trunk_and_pvid_are_read():
    """QX は書式が違うだけで意味は同じ。同じ形に寄せて持つ。"""
    parsed = configparse.parse(NECQX)
    ports = {i.key: i for i in parsed.interfaces}

    trunk = ports["gigabitethernet1/0/1"]
    assert trunk.trunk_vlans == [10, 20, 21, 22]
    assert trunk.native_vlan == 4094  # PVID がネイティブにあたる

    hybrid = ports["gigabitethernet1/0/2"]
    assert hybrid.mode == "hybrid"
    assert hybrid.trunk_vlans == [10, 20]
    assert hybrid.native_vlan == 10


JUNOS = """
set system host-name edge-sw-01
set vlans v-users vlan-id 10
set vlans v-srv vlan-id 20
set vlans v-srv description サーバーセグメント
set interfaces ge-0/0/1 unit 0 family ethernet-switching interface-mode trunk
set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members [ v-users v-srv ]
set interfaces ge-0/0/1 native-vlan-id 999
set interfaces ge-0/0/2 unit 0 family ethernet-switching interface-mode access
set interfaces ge-0/0/2 unit 0 family ethernet-switching vlan members v-users
"""


def test_junos_vlan_names_are_resolved_to_numbers():
    """Junos は VLAN を名前で参照する。番号に直さないと他機器と突き合わせられない。"""
    parsed = configparse.parse(JUNOS)
    ports = {i.key: i for i in parsed.interfaces}

    assert parsed.vlans[10] == "v-users"
    assert parsed.vlans[20] == "サーバーセグメント"

    assert ports["ge-0/0/1"].trunk_vlans == [10, 20]
    assert ports["ge-0/0/1"].native_vlan == 999
    assert ports["ge-0/0/2"].vlan == 10
    assert ports["ge-0/0/2"].trunk_vlans == []


def test_vlan_ranges_round_trip():
    """DB には畳んで入れるので、戻して同じにならないと数が狂う。"""
    values = [1, 2, 3, 7, 10, 11, 12, 4094]
    text = vlanlist.format_ranges(values)
    assert text == "1-3,7,10-12,4094"
    assert vlanlist.parse_ranges(text) == values


def test_vlan_range_parsing_ignores_junk():
    assert vlanlist.parse_ranges("10, 20 to 22, none") == [10, 20, 21, 22]
    assert vlanlist.parse_ranges("") == []
    assert vlanlist.parse_ranges("9999") == []  # 範囲外は捨てる


def test_all_column_is_resolved_against_defined_vlans():
    """`all` は番号に展開せず持つ。展開すると VLAN が増えたとき古い一覧が残る。"""
    assert vlanlist.expand_column("all", {10, 20}) == {10, 20}
    assert vlanlist.expand_column("all except 20", {10, 20, 30}) == {10, 30}
    assert vlanlist.expand_column("10,20", {10, 20, 30}) == {10, 20}
    assert vlanlist.expand_column(None, {10}) == set()
