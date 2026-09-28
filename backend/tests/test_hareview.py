"""2台で1台に見せる仕組みが、組として成り立っているか。"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import configparse, hareview
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
    irf = hapair.parse("irf domain 10\nirf member 1 priority 32\n")
    assert (irf.kind, irf.group, irf.priority) == ("irf", "10", 32)
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


def test_a_stack_is_read_as_one_failure_domain() -> None:
    """組んであるスイッチは、外から見ると1台になる。

    読めないと、落ちる範囲を数えるときに別々の機器として数えてしまう。
    2台あるから大丈夫、と読める形で、実際は1台ぶんしか無い。
    """
    vrp = "\n".join(
        [
            "VRP (R) software, Version 8.180 (CE6850 V200R005C10SPC800)",
            "sysname hw1",
            "#",
            "stack",
            " stack member 1 priority 150",
        ]
    )
    catalyst = "\n".join(
        [
            "hostname c1",
            "switch 1 provision c9300-48p",
            "switch 2 provision c9300-48p",
            "switch 1 priority 15",
        ]
    )
    assert configparse.parse(vrp).ha_pair is not None
    assert configparse.parse(vrp).ha_pair.kind == "stack"

    stacked = configparse.parse(catalyst).ha_pair
    assert stacked is not None
    assert stacked.kind == "stackwise"
    assert stacked.group == "2"
    assert stacked.priority == 15


def test_one_provisioned_switch_is_not_called_a_stack() -> None:
    # 1台だけ書いてあるのは組ではない。組んでいると読むと、冗長に見えてしまう
    text = "hostname c1\nswitch 1 provision c9300-48p\n"
    assert configparse.parse(text).ha_pair is None


def test_two_separate_stacks_are_not_treated_as_one_pair(client):
    """スタックは1台の中の話。別々の機器を「同じ組」と読まないこと。

    台数が同じというだけで組にすると、優先度がぶつかっていると言うことに
    なる。2台で1台に見せる仕組みとは、そもそも別の話。
    """
    body = (
        "hostname {name}\n"
        "switch 1 provision c9300-48p\n"
        "switch 2 provision c9300-48p\n"
        "switch 1 priority 15\n"
        "interface GigabitEthernet1/0/1\n description a\n"
    )
    files = [
        ("files", (f"{name}.txt", body.format(name=name).encode(), "text/plain"))
        for name in ("sw-01", "sw-02")
    ]
    client.post(f"{API}/configs/upload", files=files, data={"create_missing": "true"})

    report = client.get(f"{API}/topology/ha-check").json()
    # 読めていることは見せる
    assert report["groups"]
    # 機器をまたいだ突き合わせはしない
    assert report["findings"] == []


def test_the_heartbeat_address_is_not_taken_as_the_peer():
    """`peer-address heartbeat` は二重主の検出用の別の宛先（Arista Security Advisory 0161 の作例）。

    `show mlag` でも `peer-address` と `hb-peer-address` は別の項目として出る。
    """
    text = (
        "mlag configuration\n"
        "   domain-id mlagDomain\n"
        "   local-interface Vlan4094\n"
        "   peer-address 10.0.0.2\n"
        "   peer-address heartbeat 172.30.118.190 vrf MGMT\n"
        "   peer-link Port-Channel10\n"
        "   dual-primary detection delay 5 action errdisable all-interfaces\n"
    )
    pair = hapair.parse(text)
    assert pair.peer_address == "10.0.0.2"
    assert pair.link == "Port-Channel10"


#: Cisco「CLI Book 1: Cisco ASA Series General Operations」の failover の作例の形。
#: Stateful 用の口（failover link）の住所を先に書いて、取り違えないことも見る
ASA_PAIR = (
    "failover\n"
    "failover lan unit {role}\n"
    "failover lan interface fover gigabitethernet0/8\n"
    "failover link stlink gigabitethernet0/7\n"
    "failover interface ip stlink 10.9.9.1 255.255.255.0 standby 10.9.9.2\n"
    "failover interface ip fover 192.168.240.254 255.255.255.0 standby 192.168.240.253\n"
)


def test_the_asa_peer_is_the_address_on_the_other_side_of_the_failover_link():
    """前の住所は primary、standby の後ろは secondary に付く。相手はその反対側。"""
    primary = hapair.parse(ASA_PAIR.format(role="primary"))
    secondary = hapair.parse(ASA_PAIR.format(role="secondary"))
    assert primary.peer_address == "192.168.240.253"
    assert secondary.peer_address == "192.168.240.254"
    assert primary.link == "gigabitethernet0/8"


def test_the_asa_peer_is_read_from_an_ipv6_failover_link():
    pair = hapair.parse(
        "failover lan unit primary\n"
        "failover lan interface folink GigabitEthernet0/3\n"
        "failover interface ip folink 2001:a0a:b00::a0a:b70/64 standby 2001:a0a:b00::a0a:b71\n"
    )
    assert pair.peer_address == "2001:a0a:b00::a0a:b71"
