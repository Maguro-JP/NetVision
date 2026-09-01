"""2台で1台に見せる仕組みが、組として成り立っているか。"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import hareview
from app.services.configparse import hapair
from tests.conftest import API

FGT_A = (
    'config system ha\n    set group-name "FGT-HA"\n    set mode a-p\n    set priority 200\nend\n'
)
FGT_B = FGT_A.replace("priority 200", "priority 100")
ASA_P = (
    "hostname fw-a\n"
    "failover\n"
    "failover lan unit primary\n"
    "failover lan interface FO GigabitEthernet1/8\n"
)
ASA_S = "hostname fw-b\nfailover\nfailover lan unit secondary\n"
VSX_P = (
    "!Version ArubaOS-CX FL.10.09.0010\n"
    "vsx\n"
    "    system-mac 02:01:00:00:01:00\n"
    "    inter-switch-link lag 1\n"
    "    role primary\n"
)
VSX_S = VSX_P.replace("role primary", "role secondary")
MLAG = (
    "mlag configuration\n"
    "   domain-id DC1\n"
    "   peer-address 10.255.0.2\n"
    "   peer-link Port-Channel10\n"
)


def _device(db, hostname: str, text: str) -> Device:
    device = Device(hostname=hostname, vendor="x")
    db.add(device)
    db.flush()
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


def _titles(db) -> list[str]:
    return [f.title for f in hareview.review(db).findings]


def test_each_vendor_is_read_into_the_same_shape():
    assert hapair.parse(FGT_A).group == "FGT-HA"
    assert hapair.parse(FGT_A).priority == 200
    assert hapair.parse(ASA_P).role == "primary"
    assert hapair.parse(ASA_P).link == "GigabitEthernet1/8"
    assert hapair.parse(VSX_P).group == "02:01:00:00:01:00"
    assert hapair.parse(VSX_P).role == "primary"
    assert hapair.parse(MLAG).group == "DC1"
    assert hapair.parse(MLAG).peer_address == "10.255.0.2"
    assert hapair.parse("hostname sw\ninterface Gi1\n") is None


def test_a_standalone_fortigate_is_not_a_pair():
    assert hapair.parse("config system ha\n    set mode standalone\nend\n") is None


def test_a_proper_pair_gets_no_complaints(db_session):
    _device(db_session, "fw-a", FGT_A)
    _device(db_session, "fw-b", FGT_B)
    assert _titles(db_session) == []


def test_a_member_without_its_partner_is_pointed_out(db_session):
    _device(db_session, "fw-a", FGT_A)
    assert [t for t in _titles(db_session) if "相手が台帳にありません" in t]


def test_two_primaries_are_pointed_out(db_session):
    _device(db_session, "sw-a", VSX_P)
    _device(db_session, "sw-b", VSX_P)
    assert [t for t in _titles(db_session) if "両方とも primary" in t]


def test_a_proper_vsx_pair_is_quiet(db_session):
    _device(db_session, "sw-a", VSX_P)
    _device(db_session, "sw-b", VSX_S)
    assert _titles(db_session) == []


def test_equal_priorities_are_pointed_out(db_session):
    _device(db_session, "fw-a", FGT_A)
    _device(db_session, "fw-b", FGT_A)
    assert [t for t in _titles(db_session) if "優先度が両方同じ" in t]


def test_the_api_returns_the_groups(client, db_session):
    _device(db_session, "fw-a", ASA_P)
    _device(db_session, "fw-b", ASA_S)
    body = client.get(f"{API}/topology/ha-check").json()
    assert body["devices_seen"] == 2
    assert list(body["groups"].values()) == [["fw-a", "fw-b"]]
    assert body["findings"] == []
