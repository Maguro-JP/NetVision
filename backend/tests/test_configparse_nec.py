"""NEC の機器（IX / QX）を読めること。

IX と QX は書式がまったく違う。
どちらも Cisco 風に見える箇所があるので、取り違えないことも確かめる。
"""

from pathlib import Path

from app.services import configparse, policyreview
from app.services.configparse.base import Vendor

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text()


# ---------------------------------------------------------------------------
# IX シリーズ
# ---------------------------------------------------------------------------


def test_ix_is_detected_and_read():
    parsed = configparse.parse(load("ix-rt-01"))

    assert parsed.vendor is Vendor.NEC_IX
    assert parsed.hostname == "ix-rt-01"
    assert parsed.model == "IX2215"
    # 名乗りに OS の名前は無いので空。型番から付けた名前は入れない
    assert parsed.os_name is None
    assert parsed.os_version == "10.6.21"
    assert parsed.serial_number == "2215X0012345"


def test_ix_interfaces_keep_their_sub_interface_number():
    """IX は GigaEthernet0.0 のように番号まで含めて1つのインターフェース。"""
    parsed = configparse.parse(load("ix-rt-01"))
    names = [i.name for i in parsed.interfaces]

    assert "GigaEthernet0.0" in names
    assert "Loopback0.0" in names

    wan = next(i for i in parsed.interfaces if i.name == "GigaEthernet0.0")
    assert wan.description == "WAN-回線"
    assert wan.ip_address == "203.0.113.2/30"


def test_ix_vendor_name_is_nec():
    parsed = configparse.parse(load("ix-rt-01"))
    assert configparse.VENDOR_NAMES[parsed.vendor] == "NEC"


# ---------------------------------------------------------------------------
# QX シリーズ
# ---------------------------------------------------------------------------


def test_qx_is_detected_and_read():
    parsed = configparse.parse(load("qx-sw-01"))

    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "qx-sw-01"
    assert parsed.model == "QX-S5528P"
    # OS の名前は入れない。機器が何と名乗るかは機器が決めること
    assert parsed.os_name is None
    assert parsed.os_version == "5.20.99"
    assert parsed.serial_number == "210235A0L6C123000010"


def test_qx_interfaces_and_vlans():
    parsed = configparse.parse(load("qx-sw-01"))

    access = next(i for i in parsed.interfaces if i.name == "GigabitEthernet1/0/8")
    assert access.mode == "access"
    assert access.vlan == 10
    assert access.description == "client-port"

    trunk = next(i for i in parsed.interfaces if i.name == "GigabitEthernet1/0/1")
    assert trunk.mode == "trunk"

    assert parsed.vlans == {10: "USERS", 20: "SERVERS"}


def test_qx_lldp_neighbors_are_read():
    """QX の display lldp neighbor-information から結線が引けること。"""
    parsed = configparse.parse(load("qx-sw-01"))

    assert len(parsed.neighbors) == 1
    neighbor = parsed.neighbors[0]
    assert neighbor.remote_hostname == "core-sw-01"
    assert neighbor.remote_interface == "GigabitEthernet1/0/24"


def test_qx_running_config_with_sysname_is_enough():
    """running-config だけ（バナーも型番も無し）でも sysname で機器名が取れること。

    現場では display current-configuration だけが来る。hostname コマンドは無く、
    機器名は sysname に入っている。ここを Cisco 扱いすると台帳と結び付かない。
    """
    text = """
#
 sysname osaka-qx-01
#
interface GigabitEthernet1/0/1
 port link-type trunk
 port trunk permit vlan 10
#
interface Vlan-interface10
 ip address 192.168.10.254 255.255.255.0
#
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "osaka-qx-01"
    assert parsed.serial_number is None
    assert any(i.name == "GigabitEthernet1/0/1" for i in parsed.interfaces)


def test_qx_serial_from_esn_and_sentence_forms():
    esn = configparse.parse(
        "sysname qx-sw-01\ninterface GigabitEthernet1/0/1\nESN of slot 1: 210235A0L6C123000010\n"
    )
    assert esn.serial_number == "210235A0L6C123000010"

    sentence = configparse.parse(
        "sysname qx-sw-01\ninterface GigabitEthernet1/0/1\n"
        "The Serial Number of the device is: 210235A0L6C999000001\n"
    )
    assert sentence.serial_number == "210235A0L6C999000001"


def test_qx_serial_skips_none_and_power_supply():
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
Slot 1:
DEVICE_SERIAL_NUMBER : None
Power 1:
DEVICE_SERIAL_NUMBER : 210231A0PWR0000001
Chassis:
DEVICE_SERIAL_NUMBER : 210235A0L6C123000010
"""
    parsed = configparse.parse(text)
    assert parsed.serial_number == "210235A0L6C123000010"


def test_qx_does_not_treat_lldp_chassis_id_as_serial():
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  Chassis type      : MAC address
  Chassis ID        : 0011-2233-4455
  System name       : core-sw-01
  Port ID           : GigabitEthernet1/0/24
"""
    parsed = configparse.parse(text)
    assert parsed.serial_number is None


def test_qx_reads_model_from_manuinfo_when_banner_is_missing():
    text = """
sysname branch-qx-01
interface GigabitEthernet1/0/1
Slot 1:
DEVICE_NAME          : QX-S6520X
DEVICE_SERIAL_NUMBER : 210235A1ABC123456789
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.model == "QX-S6520X"
    assert parsed.serial_number == "210235A1ABC123456789"


def test_quoted_sysname_is_read():
    parsed = configparse.parse('sysname "branch-sw-01"\ninterface GigabitEthernet1/0/1\n')
    assert parsed.vendor is Vendor.NEC_QX
    assert parsed.hostname == "branch-sw-01"


def test_ix_lldp_reads_port_id_even_when_it_comes_before_system_name():
    """IX の detail は Port ID が System Name より先。順番に依存しないこと。"""
    text = """
IX Series IX2215 (magellan-sec) Software, Version 10.6.21
device-name ix-rt-01
!
interface GigaEthernet1.0
  no shutdown
!
Interface: GigaEthernet1.0
  Chassis ID subtype: MAC address
  Chassis ID: 0012.e200.1234
  Port ID subtype: Interface name
  Port ID: GigabitEthernet1/0/24
  System Name: core-sw-01
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_IX
    assert parsed.hostname == "ix-rt-01"
    assert len(parsed.neighbors) == 1
    assert parsed.neighbors[0].local_interface == "GigaEthernet1.0"
    assert parsed.neighbors[0].remote_hostname == "core-sw-01"
    assert parsed.neighbors[0].remote_interface == "GigabitEthernet1/0/24"


def test_qx_lldp_uses_port_description_when_port_id_is_a_mac():
    """Port ID が MAC のとき、ポート名は description 側にある。"""
    text = """
sysname qx-sw-01
interface GigabitEthernet1/0/1
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  Chassis type      : MAC address
  Port ID           : 0011-2233-4455
  Port description  : Ten-GigabitEthernet1/0/1
  System name       : core-sw-01
"""
    parsed = configparse.parse(text)
    assert parsed.neighbors[0].remote_hostname == "core-sw-01"
    assert parsed.neighbors[0].remote_interface == "TenGigabitEthernet1/0/1"


# ---------------------------------------------------------------------------
# 取り違えないこと
# ---------------------------------------------------------------------------


def test_cisco_is_not_mistaken_for_nec():
    """NEC の判別を Cisco より先に走らせているので、誤判定しないことを確かめる。"""
    for name in ("core-sw-01", "acc-sw-osaka-01"):
        parsed = configparse.parse(load(name))
        assert parsed.vendor in (Vendor.CISCO_IOS, Vendor.CISCO_NXOS), name


def test_nec_is_not_mistaken_for_cisco():
    assert configparse.parse(load("ix-rt-01")).vendor is Vendor.NEC_IX
    assert configparse.parse(load("qx-sw-01")).vendor is Vendor.NEC_QX


# ---------------------------------------------------------------------------
# 差分エンジンの方言
# ---------------------------------------------------------------------------


def test_necqx_negation_is_undo_not_no():
    """QX は、消すのは `no` ではなく `undo`。"""
    from app.services.configdiff import build

    current = "interface GigabitEthernet1/0/8\n description old\n port access vlan 10\n"
    target = "interface GigabitEthernet1/0/8\n description old\n"

    result = build(current, target, "nec_qx")
    assert result.verified
    assert "undo port access vlan 10" in result.script
    assert "no port access vlan" not in result.script


def test_ix_negation_is_no():
    from app.services.configdiff import build

    current = "interface GigaEthernet0.0\n description WAN\n ip address 10.0.0.1/30\n"
    target = "interface GigaEthernet0.0\n description WAN\n"

    result = build(current, target, "nec_ix")
    assert result.verified
    assert "no ip address 10.0.0.1/30" in result.script


def test_qx_vlan_definitions_are_read_from_running_config():
    """`display current-configuration` は行頭に空白が入る。

    行頭固定で見ると VLAN の定義を丸ごと取りこぼし、VLAN 台帳に名前が載らない。
    """
    text = """#
 sysname aichi-sw-01
#
 vlan 10
  name USERS
#
 vlan 20 to 22
#
interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 10
#
"""
    parsed = configparse.parse(text)
    assert parsed.vlans[10] == "USERS"
    assert set(parsed.vlans) == {10, 20, 21, 22}


def test_qx_lag_membership_is_read():
    """束ねたポートは、どの LAG のものか分からないと1本の線にまとめられない。"""
    text = """#
 sysname sw
#
interface Bridge-Aggregation1
 port link-type trunk
#
interface GigabitEthernet1/0/1
 port link-aggregation group 1
#
"""
    parsed = configparse.parse(text)
    member = next(i for i in parsed.interfaces if i.name.endswith("1/0/1"))
    assert member.lag_group == 1


def test_qx_ip_keeps_its_mask():
    """マスクを落とすと、どの範囲のアドレスなのかが台帳から消える。"""
    text = """#
 sysname sw
#
interface Vlan-interface10
 ip address 10.104.10.1 255.255.255.0
#
"""
    parsed = configparse.parse(text)
    assert parsed.interfaces[0].ip_address == "10.104.10.1/24"


def test_ix_ports_are_down_until_no_shutdown():
    """IX のポートは既定で止まっている。

    `no shutdown` を書いていないポートを「使っている」と読むと、
    空きポートの数を間違える。
    """
    text = """device-name rt
!
interface GigaEthernet0.0
  ip address 10.0.0.1/30
  no shutdown
!
interface GigaEthernet1.0
  ip address 10.0.1.1/24
!
"""
    parsed = configparse.parse(text)
    used = next(i for i in parsed.interfaces if i.name.endswith("0.0"))
    spare = next(i for i in parsed.interfaces if i.name.endswith("1.0"))
    assert used.enabled is True
    assert spare.enabled is False


def test_ix_lldp_table_with_local_interface_header():
    """見出しが `Local Interface` の実機がある。読み飛ばすと隣接が0本になる。"""
    text = """device-name rt
!
show lldp neighbor
Local Interface  Chassis ID         Port ID                System Name
GigaEthernet0.0  00:11:22:33:44:55  GigabitEthernet1/0/24  aichi-sw-01
"""
    parsed = configparse.parse(text)
    assert [(n.local_interface, n.remote_hostname) for n in parsed.neighbors] == [
        ("GigaEthernet0.0", "aichi-sw-01")
    ]


def test_hostname_falls_back_to_the_prompt():
    """show 出力だけのファイルでも、プロンプトの機器名で台帳と結び付ける。"""
    qx = configparse.parse(
        "<aichi-sw-01>display version\n"
        "Software, Version 7.1.070, Release 3506P05\nNEC QX-S5528P uptime is 1 day\n"
    )
    assert qx.hostname == "aichi-sw-01"
    assert qx.model == "QX-S5528P"

    ix = configparse.parse(
        "mie-rt-01# show version\nIX Series IX2215 (magellan-sec) Software, Version 10.6.21\n"
    )
    assert ix.hostname == "mie-rt-01"


def test_ix_ipsec_is_read_into_the_same_shape_as_cisco():
    """拠点間 VPN の強さを、Cisco と同じ器へ移す。

    そうすると DES や MD5 が残っているときの指摘が、判断層を足さずに
    そのまま出る。合言葉そのものは持たない。
    """
    parsed = configparse.parse((FIXTURES / "ix-rt-01.txt").read_text())
    crypto = parsed.crypto
    assert crypto is not None
    policy = crypto.ike_policies[0]
    assert (policy.number, policy.encryption, policy.hash) == ("ike-prop", "aes", "sha256")
    # 鍵の作り方は長さで書かれる。判断の側と同じ群の番号に直す
    assert policy.group == "14"
    assert crypto.transform_sets[0].transforms == ["esp-aes", "esp-sha256-hmac"]
    assert crypto.maps[0].peers == ["203.0.113.10"]
    assert crypto.applied == [("Tunnel0.0", "ipsec-map")]
    assert crypto.keyed_peers == ["203.0.113.10"]
    assert "ipsec-key" not in str(crypto)


def test_an_old_ix_tunnel_is_pointed_out():
    text = (FIXTURES / "ix-rt-01.txt").read_text()
    old = text.replace(
        "ike proposal ike-prop encryption aes hash sha256 group 2048-bit",
        "ike proposal ike-prop encryption des hash md5 group 768-bit",
    )
    parsed = configparse.parse(old)
    found = [f.title for f in policyreview.review(parsed, old)]
    assert [t for t in found if "破られている方式" in t]
    assert [t for t in found if "鍵の作り方が古い" in t]


def test_the_range_an_ipsec_map_points_at_is_not_called_unused():
    """IPsec から参照している ACL を「どこにも当たっていない」と言わない。"""
    text = (FIXTURES / "ix-rt-01.txt").read_text()
    parsed = configparse.parse(text)
    found = [f.title for f in policyreview.review(parsed, text)]
    assert not [t for t in found if "ipsec-acl" in t and "当たっていません" in t]


def test_the_ix_default_route_is_read():
    """IX の既定経路は宛先を書かず default と書く。

    支店のルーターでいちばん大事な1本で、読めないと「既定の経路が
    見当たりません」を書いてある機器にまで出してしまう。
    """
    text = """IX Series IX2215 (magellan-sec) Software, Version 10.6.21
device-name aichi-rt-01
!
ip route default GigaEthernet0.0 10.1.0.254
ip route 192.168.20.0/24 10.1.0.253
ipv6 route default GigaEthernet0.0 2001:db8::1
!
interface GigaEthernet0.0
  ip address 10.1.0.1/24
  no shutdown
"""
    parsed = configparse.parse(text)
    assert parsed.vendor is Vendor.NEC_IX
    routes = {r.destination: r for r in parsed.routes}
    assert routes["0.0.0.0/0"].next_hop == "10.1.0.254"
    assert routes["0.0.0.0/0"].interface == "GigaEthernet0.0"
    assert routes["::/0"].next_hop == "2001:db8::1"
    # 宛先を書く形も、これまで通り読める
    assert routes["192.168.20.0/24"].next_hop == "10.1.0.253"


def test_a_vlan_taken_off_the_trunk_is_no_longer_permitted() -> None:
    """QX は許可を足していく形で書く。外す行は頭に undo が付く。

    そこを読み落とすと、通していない VLAN を通していることにしてしまう。
    両端で VLAN がそろっているかの突き合わせが、そのぶん狂う。
    """
    text = (
        "sysname qx-01\n#\n"
        "interface GigabitEthernet1/0/1\n"
        " port link-type trunk\n"
        " port trunk permit vlan 10 20\n"
        " port trunk permit vlan 30\n"
        " undo port trunk permit vlan 20\n"
        "#\n"
    )
    assert configparse.parse(text).interfaces[0].trunk_vlans == [10, 30]


def test_taking_everything_off_the_trunk_leaves_nothing() -> None:
    text = (
        "sysname qx-01\n#\n"
        "interface GigabitEthernet1/0/1\n"
        " port link-type trunk\n"
        " port trunk permit vlan 10 20\n"
        " undo port trunk permit vlan all\n"
        "#\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.trunk_vlans == []
    assert port.trunk_all is False


def test_qx_trunk_permit_vlan_reads_the_manual_example_and_adds_up_lines():
    """QX の `port trunk permit vlan` は、`to` の範囲と、何行かに分けた書き方を足し合わせて読む。

    出典: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」10.2.7 port trunk permit vlan
    （`port trunk permit vlan { vlan-id-list | all }`。範囲は vlan-id1 to vlan-id2、1〜4094。
    繰り返し実行すると、各 vlan-id-list で指定した VLAN が通る。undo で外す。
    例: `port trunk permit vlan 2 4 50 to 100`）。
    """
    base = load("qx-sw-01").split("interface ")[0]
    text = (
        base + "interface GigabitEthernet1/0/1\n"
        " port link-type trunk\n"
        " port trunk permit vlan 2 4 50 to 100\n"
        " port trunk permit vlan 200\n"
        " undo port trunk permit vlan 4\n"
        "#\n"
    )
    parsed = configparse.parse(text)
    port = next(i for i in parsed.interfaces if i.name.endswith("1/0/1"))
    assert port.trunk_vlans == [2, *range(50, 101), 200]
