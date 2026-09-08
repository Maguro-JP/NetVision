"""H3C を、NEC QX とは別の製品として読むこと。

NEC QX は H3C の OEM で、書き方は今日のところ重なる。だが別の製品である。
別の製品を「似ているから」と同じ読み手に通すと、片方の都合で直した読み方が
もう片方にも黙って効く。読み手も判別も、この機種のものとして独立して持つ。
"""

from app.services import configparse
from app.services.configparse import h3c, nec
from app.services.configparse.base import Vendor

H3C = """#
 version 7.1.070, Release 6127P02
#
 sysname h3c-sw-01
#
vlan 10
 name USERS
#
vlan 20 to 22
#
ip vpn-instance CUST
 route-distinguisher 65000:1
#
interface Vlan-interface10
 ip address 10.0.10.1 255.255.255.0
 ip address 10.0.11.1 24 sub
#
interface GigabitEthernet1/0/1
 port link-mode bridge
 description to core
 port link-type trunk
 port trunk permit vlan 10 20
 undo port trunk permit vlan 20
 port trunk pvid vlan 999
 port link-aggregation group 1
#
interface GigabitEthernet1/0/2
 port link-mode bridge
 port link-type trunk
#
interface GigabitEthernet1/0/5
 port link-mode bridge
 port access vlan 10
 shutdown
#
interface GigabitEthernet1/0/24
 port link-mode route
 ip address dhcp-alloc
 ip binding vpn-instance CUST
#
interface Bridge-Aggregation1
 link-aggregation mode dynamic
#
 ip route-static 0.0.0.0 0 10.0.10.254
#
 H3C S5560X-30C-EI
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  System name          : core-sw-01
  Port ID              : GigabitEthernet1/0/24
"""


def _parsed():
    return configparse.parse(H3C)


def _port(name: str):
    return next(port for port in _parsed().interfaces if port.name == name)


def test_the_reader_does_not_go_through_the_nec_reader() -> None:
    """独立していること。NEC 側を直したときに、こちらが黙って変わらないため。"""
    import inspect

    source = inspect.getsource(h3c)
    assert "nec." not in source
    assert "from app.services.configparse import" in source
    assert "nec" not in [name for name in dir(h3c) if not name.startswith("_")]


def test_h3c_is_recorded_as_h3c() -> None:
    parsed = _parsed()
    assert parsed.vendor is Vendor.H3C
    assert configparse.VENDOR_NAMES[parsed.vendor] == "H3C"
    assert parsed.hostname == "h3c-sw-01"


def test_the_model_and_the_version_are_read() -> None:
    """型番が読めないと、保守期限も脆弱性も引けない。"""
    parsed = _parsed()
    assert parsed.model == "S5560X-30C-EI"
    assert parsed.os_version == "7.1.070"


def test_the_model_is_read_from_display_version_too() -> None:
    text = (
        "H3C Comware Software, Version 7.1.070, Release 6127P02\n"
        "H3C S6520X-30QC-EI uptime is 1 week\n sysname x\n"
    )
    parsed = configparse.parse(text)
    assert parsed.model == "S6520X-30QC-EI"
    assert parsed.os_version == "7.1.070"


def test_vlans_including_the_range_form_are_read() -> None:
    assert _parsed().vlans == {10: "USERS", 20: "", 21: "", 22: ""}


def test_a_vlan_taken_off_the_trunk_is_no_longer_permitted() -> None:
    port = _port("GigabitEthernet1/0/1")
    assert port.mode == "trunk"
    assert port.trunk_vlans == [10]
    assert port.native_vlan == 999
    assert port.lag_group == 1
    assert port.description == "to core"


def test_a_trunk_that_permits_nothing_carries_only_vlan_1_untagged() -> None:
    """書いていないトランクは VLAN 1 だけが通る既定。全許可とはみなさない。"""
    port = _port("GigabitEthernet1/0/2")
    assert port.native_vlan == 1
    assert port.trunk_all is False


def test_an_access_port_that_is_shut_is_read_as_shut() -> None:
    port = _port("GigabitEthernet1/0/5")
    assert port.vlan == 10
    assert port.enabled is False


def test_a_routed_port_with_a_dhcp_address_is_not_an_empty_port() -> None:
    """貰う設定は「無い」ではない。空欄にすると付け忘れと見分けが付かない。"""
    port = _port("GigabitEthernet1/0/24")
    assert port.mode == "routed"
    assert port.ip_address is None
    assert port.address_source == "dhcp"
    assert port.vrf == "CUST"


def test_a_second_address_does_not_overwrite_the_first() -> None:
    port = _port("Vlan-interface10")
    assert port.ip_address == "10.0.10.1/24"
    assert port.extra_addresses == ["10.0.11.1/24"]


def test_the_bundle_and_the_vrf_and_the_route_and_the_neighbour_are_read() -> None:
    parsed = _parsed()
    assert _port("Bridge-Aggregation1").lag_mode == "active"
    assert [(v.name, v.rd) for v in parsed.vrfs] == [("CUST", "65000:1")]
    assert [(r.destination, r.next_hop) for r in parsed.routes] == [("0.0.0.0/0", "10.0.10.254")]
    assert [
        (n.local_interface, n.remote_hostname, n.remote_interface) for n in parsed.neighbors
    ] == [("GigabitEthernet1/0/1", "core-sw-01", "GigabitEthernet1/0/24")]


def test_a_nec_config_is_still_nec() -> None:
    nec_text = (
        "#\n version 7.1.070, Release 6127P02\n#\n sysname qx-sw-01\n#\n NEC UNIVERGE QX-S5530\n#\n"
    )
    assert configparse.parse(nec_text).vendor is Vendor.NEC_QX


def test_the_os_banner_does_not_decide_the_product() -> None:
    """NEC QX の display version も、1行目は「H3C Comware」と出る。

    それを見て H3C と呼ぶと、NEC の機器がまるごと H3C になる。製品の印
    （型番・VENDOR_NAME・型番の前の会社名）がその機器の名前で、OS の名前は
    その機器の名前ではない。OEM 元だから、買った先だから、とは考えない。
    """
    nec_unit = (
        "H3C Comware Platform Software\n"
        "Comware Software, Version 5.20.99, Release 6108\n"
        "NEC QX-S5528P Routing Switch uptime is 3 days\n"
        " sysname qx-sw-01\n"
    )
    h3c_unit = (
        "H3C Comware Software, Version 7.1.070, Release 6127P02\n"
        "H3C S5560X-30C-EI uptime is 1 week\n"
        " sysname h3c-sw-01\n"
    )
    assert configparse.parse(nec_unit).vendor is Vendor.NEC_QX
    assert configparse.parse(h3c_unit).vendor is Vendor.H3C


def test_the_os_banner_alone_is_h3c_when_nothing_names_another_product() -> None:
    """製品の印が無く、OS の名乗りだけなら、その OS の作り手の機器とみなす。"""
    text = "H3C Comware Software, Version 7.1.070\n sysname x\n"
    assert configparse.parse(text).vendor is Vendor.H3C


def test_a_config_without_a_name_is_not_called_h3c() -> None:
    """書き方が同じなので、名乗りが無いものを H3C だと決めない。"""
    plain = "#\n sysname sw-01\n#\nvlan 10\n name USERS\n#\n"
    assert configparse.parse(plain).vendor is Vendor.NEC_QX


def test_the_two_readers_agree_on_what_they_both_read_today() -> None:
    """独立させても、今日は同じ言語なので同じ結果になるはず。

    ここがずれたら、どちらかの読み手が壊れている（意図して分けた差ではない）。
    """
    mine = configparse.parse(H3C)
    theirs = nec.parse(H3C, Vendor.NEC_QX)
    assert mine.vlans == theirs.vlans
    # mode は比べない。こちらは `port link-mode route` を読んで routed にするが、
    # NEC 側は読まない。意図して分けた差なので、ここで揃えることは求めない
    assert [(p.name, p.vlan, p.trunk_vlans, p.native_vlan) for p in mine.interfaces] == [
        (p.name, p.vlan, p.trunk_vlans, p.native_vlan) for p in theirs.interfaces
    ]


def test_the_commands_to_get_there_can_be_written() -> None:
    """消し方は undo。差分もそのまま出せる。"""
    from app.services.configdiff import build

    future = H3C.replace(" port trunk permit vlan 10 20\n", " port trunk permit vlan 10 20 30\n")
    diff = build(H3C, future, vendor=Vendor.H3C)
    assert diff.verified
    assert "port trunk permit vlan" in diff.script
