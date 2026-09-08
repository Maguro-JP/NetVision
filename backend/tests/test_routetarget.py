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
