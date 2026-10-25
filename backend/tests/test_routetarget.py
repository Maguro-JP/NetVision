"""VRF の経路の相互流入（route-target）。片側だけの import は経路が流れない。"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.models.vlan import DeviceVrf
from app.services import topology
from app.services.configparse import routetarget
from tests.conftest import API

CISCO = """hostname pe-01
vrf definition CUST-A
 rd 65000:10
 address-family ipv4
  route-target export 65000:10
  route-target import 65000:10
  route-target import 65000:99
 exit-address-family
!
ip vrf OLD
 rd 65000:20
 route-target both 65000:20
"""

JUNOS = """set system host-name pe-02
set routing-instances CUST-A instance-type vrf
set routing-instances CUST-A route-distinguisher 65000:10
set routing-instances CUST-A vrf-target target:65000:10
set routing-instances CUST-B vrf-target import target:65000:30
set routing-instances CUST-B vrf-target export target:65000:31
"""

NECQX = """sysname pe-03
ip vpn-instance CUST-A
 route-distinguisher 65000:10
 vpn-target 65000:10 import-extcommunity
 vpn-target 65000:10 export-extcommunity
 vpn-target 65000:99 import-extcommunity
"""


def test_cisco_targets_are_read_even_inside_the_address_family():
    targets = routetarget.parse(CISCO)
    assert targets["CUST-A"].exports == {"65000:10"}
    assert targets["CUST-A"].imports == {"65000:10", "65000:99"}
    assert targets["OLD"].imports == targets["OLD"].exports == {"65000:20"}


def test_junos_targets_drop_the_target_prefix_so_they_compare_with_cisco():
    targets = routetarget.parse(JUNOS)
    assert targets["CUST-A"].imports == targets["CUST-A"].exports == {"65000:10"}
    assert targets["CUST-B"].imports == {"65000:30"}
    assert targets["CUST-B"].exports == {"65000:31"}


def test_necqx_targets_are_read():
    targets = routetarget.parse(NECQX)
    assert targets["CUST-A"].imports == {"65000:10", "65000:99"}
    assert targets["CUST-A"].exports == {"65000:10"}


def _device(db, hostname: str, text: str, vrfs: list[str]) -> Device:
    device = Device(hostname=hostname, vendor="x")
    db.add(device)
    db.flush()
    for name in vrfs:
        db.add(DeviceVrf(device_id=device.id, name=name))
    db.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=hostname,
            filename=f"{hostname}.txt",
            content=text,
            sha256=hostname.ljust(64, "0")[:64],
        )
    )
    db.commit()
    return device


def test_an_import_nobody_exports_is_marked_across_devices(db_session, client):
    _device(db_session, "pe-01", CISCO, ["CUST-A", "OLD"])
    _device(db_session, "pe-03", NECQX, ["CUST-A"])

    views = {v.name: v for v in topology.vrf_summary(db_session)}
    # 65000:99 は 2 台とも import しているが、誰も export していない
    assert views["CUST-A"].orphan_imports == ["65000:99"]
    assert views["OLD"].orphan_imports == []

    body = {v["name"]: v for v in client.get(f"{API}/topology/vrfs").json()}
    assert body["CUST-A"]["orphan_imports"] == ["65000:99"]


#: Arista EOS User Manual「Inter-VRF Local Route Leaking」の書き方
ARISTA = """hostname pe-04
vrf instance vrf-red
!
router bgp 65001
   neighbor 10.0.0.1 remote-as 65002
   vrf vrf-red
      rd 1:1
      route-target export vpn-ipv4 10:10
      route-target export evpn 10:1
      route-target export vpn-ipv4 route-map EXPORT_V4
   vrf vrf-blue
      rd 2:2
      route-target import vpn-ipv4 10:10
   address-family evpn
      route-target import 99:99
!
"""


def test_arista_route_targets_under_router_bgp_are_read():
    """Arista は `router bgp` の下に書く。読まないと、片側だけの import を見逃す。"""
    parsed = routetarget.parse(ARISTA)
    assert parsed["vrf-red"].exports == {"10:10", "10:1"}
    assert parsed["vrf-blue"].imports == {"10:10"}


def test_the_address_family_word_and_route_maps_are_not_values():
    parsed = routetarget.parse(ARISTA)
    values = {v for targets in parsed.values() for v in targets.imports | targets.exports}
    assert not values & {"vpn-ipv4", "evpn", "route-map"}
    # VRF の外（`address-family evpn` の下）の値を、直前の VRF のものにしない
    assert "99:99" not in values


def test_an_automatic_junos_target_is_not_compared():
    """`vrf-target auto` は機器が決める値。`auto` 同士を比べても意味が無い。"""
    parsed = routetarget.parse("set routing-instances EVPN-1 vrf-target auto\n")
    assert parsed["EVPN-1"].imports == set()
    assert parsed["EVPN-1"].exports == set()


def test_necqx_targets_follow_the_command_manual():
    """向きを省いた `vpn-target` は both。1行に並べた複数のターゲットも全部読む。

    出典: NEC「UNIVERGE QX-S6700X/QX-S7500シリーズ コマンドマニュアル」2.2 版の
    MCE 1.1.11 vpn-target（`vpn-target vpn-target&<1-8> [ both | export-extcommunity |
    import-extcommunity ]`。「both パラメータ、export-extcommunity パラメータ、
    import-extcommunity パラメータが指定されない場合、デフォルトで both パラメータが
    指定されます」）。
    """
    parsed = routetarget.parse(
        "ip vpn-instance vpn1\n"
        " route-distinguisher 100:1\n"
        " vpn-target 1:1\n"
        " vpn-target 3:3 4:4 export-extcommunity\n"
        " vpn-target 5:5 both\n"
    )
    assert parsed["vpn1"].imports == {"1:1", "5:5"}
    assert parsed["vpn1"].exports == {"1:1", "3:3", "4:4", "5:5"}
