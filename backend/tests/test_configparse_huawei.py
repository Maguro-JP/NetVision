"""ファーウェイ VRP を、NEC QX と取り違えないこと。

VRP と NEC QX は書き方が重なる箇所がある。メーカーを取り違えると、
保守期限も脆弱性も別のメーカーのもので判定してしまい、しかも黙って通る。
"""

from __future__ import annotations

from app.services import configparse
from app.services.configparse.base import Vendor

_VRP = "\n".join(
    [
        "<hw1>display version",
        "Huawei Versatile Routing Platform Software",
        "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
        "sysname hw1",
        "#",
        "vlan 10",
        " description USERS",
        "#",
        "interface 10GE1/0/1",
        " port link-type trunk",
        " port trunk allow-pass vlan 10 20",
        "#",
        "interface Vlanif10",
        " ip address 10.0.10.1 255.255.255.0",
        "#",
        "ip route-static 0.0.0.0 0.0.0.0 10.0.10.254",
    ]
)


def test_vrp_is_read_as_huawei() -> None:
    parsed = configparse.parse(_VRP)
    assert parsed.vendor is Vendor.HUAWEI
    assert parsed.hostname == "hw1"
    assert parsed.model == "CE6850"
    assert parsed.os_version == "8.180"
    assert parsed.vlans == {10: "USERS"}

    ports = {item.name: item for item in parsed.interfaces}
    assert ports["Vlanif10"].ip_address == "10.0.10.1/24"
    assert [route.destination for route in parsed.routes] == ["0.0.0.0/0"]


def test_a_config_without_a_vrp_banner_is_still_read_as_nec_qx() -> None:
    text = "\n".join(
        [
            "sysname qx1",
            "#",
            "vlan 10",
            " name USERS",
            "#",
            "interface GigabitEthernet1/0/1",
            " port link-type trunk",
        ]
    )
    assert configparse.detect_vendor(text) is Vendor.NEC_QX


def test_an_unreadable_model_stays_empty_rather_than_blank() -> None:
    # 「読めなかった」を空文字にすると、型番が空欄の機器として台帳に入る
    parsed = configparse.parse("sysname qx1\n#\nvlan 10\n name USERS\n")
    assert parsed.model is None


def test_the_vrp_address_pool_is_read() -> None:
    # VRP は `ip pool` と書く
    text = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "ip pool USERS",
            " network 10.0.10.0 mask 255.255.255.0",
            " gateway-list 10.0.10.1",
            " dns-list 10.0.99.10",
            " excluded-ip-address 10.0.10.2 10.0.10.10",
        ]
    )
    dhcp = configparse.parse(text).dhcp
    assert dhcp is not None
    pool = dhcp.pools[0]
    assert pool.network == "10.0.10.0 255.255.255.0"
    assert pool.default_routers == ["10.0.10.1"]
    assert pool.dns_servers == ["10.0.99.10"]
    assert dhcp.excluded == [("10.0.10.2", "10.0.10.10")]


def test_eth_trunk_membership_is_read() -> None:
    # VRP は `eth-trunk 1` と書く
    text = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "interface 10GE1/0/1",
            " eth-trunk 1",
            "#",
            "interface 10GE1/0/2",
            " eth-trunk 1",
        ]
    )
    parsed = configparse.parse(text)
    assert [item.lag_group for item in parsed.interfaces] == [1, 1]
    assert "LAG" in {item.name for item in parsed.features}


def test_the_local_users_count_as_aaa() -> None:
    text = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "aaa",
            " local-user admin privilege level 15",
        ]
    )
    assert "AAA" in {item.name for item in configparse.parse(text).features}


def test_the_vlans_a_trunk_passes_are_read_in_huaweis_words() -> None:
    """VRP は通す VLAN を `allow-pass` と書く。

    読めていないと、両端で通す VLAN がそろっているかを比べられない。
    比べられないのに何も出ないと、そろっていると読めてしまう。
    """
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "interface 10GE1/0/1\n"
        " port link-type trunk\n"
        " port trunk allow-pass vlan 10 20\n"
        " port trunk allow-pass vlan 30 to 32\n"
        "#\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.trunk_vlans == [10, 20, 30, 31, 32]


def test_a_vlan_taken_off_the_trunk_is_no_longer_passed() -> None:
    """この書き方は足していく形なので、外す行を読み落とすと多く見える。"""
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "interface 10GE1/0/1\n"
        " port link-type trunk\n"
        " port trunk allow-pass vlan 10 20 30\n"
        " undo port trunk allow-pass vlan 20\n"
        "#\n"
    )
    assert configparse.parse(text).interfaces[0].trunk_vlans == [10, 30]


def test_passing_everything_is_kept_as_everything() -> None:
    """番号に展開すると、あとで VLAN が増えたときに古い一覧が残る。"""
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "interface 10GE1/0/1\n port link-type trunk\n port trunk allow-pass vlan all\n#\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.trunk_all is True
    assert port.trunk_vlans == []


def test_the_reader_does_not_go_through_the_nec_reader() -> None:
    """独立していること。NEC 側を直したときに、こちらが黙って変わらないため。"""
    import inspect

    from app.services.configparse import huawei

    assert "nec." not in inspect.getsource(huawei)


def test_a_hybrid_port_is_read_in_huaweis_word_order() -> None:
    """VRP は tagged / untagged を番号の前に置く。"""
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "interface 10GE1/0/1\n"
        " port link-type hybrid\n"
        " port hybrid tagged vlan 10 20\n"
        " port hybrid untagged vlan 999\n"
        " port hybrid pvid vlan 999\n"
        "#\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.trunk_vlans == [10, 20, 999]
    assert port.native_vlan == 999


def test_an_access_port_written_as_default_vlan_is_read() -> None:
    """VRP のアクセス VLAN は `port default vlan`。"""
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "interface GigabitEthernet0/0/5\n port link-type access\n port default vlan 10\n#\n"
    )
    port = configparse.parse(text).interfaces[0]
    assert port.mode == "access"
    assert port.vlan == 10


def test_vlan_batch_and_the_rd_under_ipv4_family_are_read() -> None:
    text = (
        "VRP (R) software, Version 8.180 (S5735 V200R019C10SPC500)\n"
        "sysname hw-01\n#\n"
        "vlan batch 10 20 30 to 32\n#\n"
        "ip vpn-instance CUST\n ipv4-family\n  route-distinguisher 65000:1\n#\n"
        "interface Eth-Trunk1\n mode lacp-static\n#\n"
    )
    parsed = configparse.parse(text)
    assert set(parsed.vlans) == {10, 20, 30, 31, 32}
    assert [(v.name, v.rd) for v in parsed.vrfs] == [("CUST", "65000:1")]
    assert parsed.interfaces[0].lag_mode == "active"


def test_a_running_config_without_the_banner_is_still_read_as_vrp() -> None:
    """`display current-configuration` には名乗りが出ないことが多い。

    名乗りだけで決めると NEC QX として読まれ、allow-pass の VLAN が読めずに
    黙って落ちる。VRP にしか無い書き方（書き方の指紋）で見分ける。
    """
    bare = (
        "sysname hw-01\n#\nvlan 10\n description USERS\n#\n"
        "interface 10GE1/0/1\n port link-type trunk\n port trunk allow-pass vlan 10 20\n#\n"
        "interface Vlanif10\n ip address 10.0.10.1 255.255.255.0\n#\n"
    )
    parsed = configparse.parse(bare)
    assert parsed.vendor is Vendor.HUAWEI
    assert parsed.interfaces[0].trunk_vlans == [10, 20]
    assert parsed.vlans == {10: "USERS"}


def test_a_nec_config_with_its_own_forms_is_not_taken_over() -> None:
    """NEC QX にしか無い書き方が出ていれば、それは NEC の config。"""
    nec = (
        "sysname qx-01\n#\ninterface GigabitEthernet1/0/1\n port link-type trunk\n"
        " port trunk permit vlan 10\n#\ninterface Vlan-interface10\n"
        " ip address 10.0.10.1 255.255.255.0\n#\n"
    )
    assert configparse.detect_vendor(nec) is Vendor.NEC_QX


def test_the_size_of_the_port_is_read_from_the_mtu_line():
    """VRP は口の大きさを `mtu` で書く。IPv6 だけの `ipv6 mtu` は別物で、混ぜない。

    出典: Huawei NetEngine 8000 F8 Configuration Guide「MTU Configuration」
    （IPv4 は `mtu mtu`、IPv6 は `ipv6 mtu mtu` と、別の命令として書かれている）
    """
    text = "\n".join(
        [
            "sysname hw2",
            "#",
            "interface 10GE1/0/1",
            " mtu 9000",
            "#",
            "interface Vlanif10",
            " ip address 10.0.10.1 255.255.255.0",
            " ipv6 mtu 1280",
            "#",
        ]
    )
    parsed = configparse.parse(text)
    ports = {i.name: i for i in parsed.interfaces}
    assert ports["10GE1/0/1"].mtu == 9000
    assert ports["Vlanif10"].mtu is None
