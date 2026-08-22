"""「ここに機器を1台足したら、どう書くか」を起こす。

増設の設計は毎回同じ手順を踏む。空きポートを探し、使っていない VLAN 番号と
セグメントを決め、上位と下がりの両方に矛盾なく書く。手で書くと片側だけ直る。
その手順を、上位機器の Config を根拠に機械的に踏めることを確かめる。
"""

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import addition
from app.services.addition import AdditionRequest, pick_subnet, pick_vlan_id
from tests.conftest import API

UPSTREAM = """hostname core-sw-01
!
vlan 10
 name OFFICE
!
vlan 20
 name WIRELESS
!
interface GigabitEthernet1/0/1
 description to edge-sw-01
 switchport mode trunk
!
interface GigabitEthernet1/0/2
!
interface GigabitEthernet1/0/3
!
interface Vlan10
 ip address 10.10.10.1 255.255.255.0
!
interface Vlan20
 ip address 10.10.20.1 255.255.255.0
!
router ospf 1
 router-id 10.0.0.1
 network 10.10.10.0 0.0.0.255 area 0
 network 10.10.20.0 0.0.0.255 area 0
 passive-interface default
!
ip route 0.0.0.0 0.0.0.0 10.10.10.254
"""


@pytest.fixture()
def upstream(db_session):
    device = Device(hostname="core-sw-01", vendor="Cisco", model="C9500-40X")
    db_session.add(device)
    db_session.flush()
    db_session.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=device.hostname,
            filename="core-sw-01.txt",
            content=UPSTREAM,
            sha256="a" * 64,
        )
    )
    db_session.commit()
    return device


def _plan(db, upstream, **overrides):
    request = AdditionRequest(
        upstream_device_id=upstream.id,
        hostname=overrides.pop("hostname", "floor3-sw-01"),
        purpose=overrides.pop("purpose", "FLOOR3"),
        **overrides,
    )
    return addition.build(db, request)


def test_the_vlan_number_continues_the_existing_step():
    """10刻みで採番されているなら、次も10刻みで続ける。"""
    assert pick_vlan_id({10, 20}) == 30
    assert pick_vlan_id({10, 20, 30}) == 40
    assert pick_vlan_id(set()) == 10


def test_a_finer_step_is_kept_as_it_is():
    """1刻みで詰めている台帳に、いきなり10飛ばしを混ぜない。"""
    assert pick_vlan_id({100, 101, 102}) == 103


def test_the_segment_follows_the_existing_numbering():
    assert pick_subnet(["10.10.10.0/24", "10.10.20.0/24"], 30) == "10.10.30.0/24"


def test_no_segment_is_invented_out_of_nothing():
    """当てずっぽうで割り当てると他拠点とぶつかる。分からないなら黙る。"""
    assert pick_subnet([], 30) is None


def test_it_decides_the_vlan_segment_and_addresses(db_session, upstream):
    plan = _plan(db_session, upstream)
    assert plan.vlan_id == 30
    assert plan.subnet == "10.10.30.0/24"
    assert plan.gateway_ip == "10.10.30.1"
    assert plan.device_ip == "10.10.30.2"


def test_every_decision_carries_a_reason(db_session, upstream):
    plan = _plan(db_session, upstream)
    assert plan.decisions
    assert all(decision.why for decision in plan.decisions)
    topics = {decision.topic for decision in plan.decisions}
    assert {"VLAN 番号", "セグメント", "アドレス", "上位のポート", "経路"} <= topics


def test_it_picks_a_port_that_looks_free(db_session, upstream):
    """description も IP も無く、隣接も見えていないポートを候補にする。"""
    plan = _plan(db_session, upstream)
    assert plan.upstream_interface == "GigabitEthernet1/0/2"


def test_it_follows_the_existing_routing_style(db_session, upstream):
    """OSPF で回している構成に、静的経路を足したりしない。"""
    plan = _plan(db_session, upstream)
    assert plan.routing == "ospf"
    assert "router ospf 1" in plan.upstream_config
    assert "network 10.10.30.0 0.0.0.255 area 0" in plan.upstream_config


def test_a_passive_default_carries_over_to_the_new_segment(db_session, upstream):
    """端末しか居ないセグメントから OSPF を喋らせても、余計な隣接ができるだけ。"""
    plan = _plan(db_session, upstream)
    assert "passive-interface Vlan30" in plan.upstream_config


def test_the_minimum_that_connects_comes_first(db_session, upstream):
    """急ぐときは 1 だけ入れれば繋がる、と読める順に並べる。"""
    body = _plan(db_session, upstream).device_config
    assert body.index("1. 最低限つながる分") < body.index("2. 経路")
    assert body.index("2. 経路") < body.index("3. セキュリティ")


def test_both_sides_are_written_and_agree(db_session, upstream):
    plan = _plan(db_session, upstream)
    assert "vlan 30" in plan.upstream_config
    assert "vlan 30" in plan.device_config
    # 上位はゲートウェイ、足す側は2番目
    assert "ip address 10.10.30.1 255.255.255.0" in plan.upstream_config
    assert "ip address 10.10.30.2 255.255.255.0" in plan.device_config
    assert "ip default-gateway 10.10.30.1" in plan.device_config


def test_the_new_device_gets_the_least_security_it_needs(db_session, upstream):
    plan = _plan(db_session, upstream)
    assert "transport input ssh" in plan.device_config
    assert "no ip http server" in plan.device_config
    # 既存から読み取れない値は、埋めずに要記入と分かる形で残す
    assert addition.TODO in plan.device_config


def test_what_to_verify_is_listed(db_session, upstream):
    plan = _plan(db_session, upstream)
    assert any("ping" in check for check in plan.checks)
    assert any("LLDP" in check for check in plan.checks)


def test_an_explicit_wish_is_honoured_but_a_clash_is_called_out(db_session, upstream):
    plan = _plan(db_session, upstream, vlan_id=10)
    assert plan.vlan_id == 10
    assert any("既に使われて" in warning for warning in plan.warnings)


def test_without_a_config_it_says_so_instead_of_guessing(db_session):
    device = Device(hostname="unknown-sw", vendor="Cisco", model="C9200")
    db_session.add(device)
    db_session.commit()
    plan = addition.build(
        db_session, AdditionRequest(upstream_device_id=device.id, hostname="new-sw-01")
    )
    assert any("取り込まれていません" in warning for warning in plan.warnings)


def test_a_redundant_upstream_is_flagged(db_session, upstream):
    """片側だけ入れると、切り替わらない状態が出来上がる。"""
    upstream_config = UPSTREAM.replace(
        "interface Vlan10\n ip address 10.10.10.1 255.255.255.0",
        "interface Vlan10\n ip address 10.10.10.2 255.255.255.0\n standby 10 ip 10.10.10.1",
    )
    db_session.add(
        ConfigSnapshot(
            device_id=upstream.id,
            hostname=upstream.hostname,
            filename="core-sw-01-2.txt",
            content=upstream_config,
            sha256="b" * 64,
        )
    )
    db_session.commit()
    plan = _plan(db_session, upstream)
    assert any("HSRP" in warning for warning in plan.warnings)


def test_an_unsupported_vendor_gets_the_decisions_not_a_guessed_syntax(db_session, upstream):
    plan = _plan(db_session, upstream, vendor="Juniper")
    assert "起こしていません" in plan.device_config
    assert "10.10.30.2" in plan.device_config


def test_the_api_returns_the_plan_with_its_reasons(client, db_session, upstream):
    res = client.post(
        f"{API}/devices/{upstream.id}/addition",
        json={"hostname": "floor3-sw-01", "purpose": "FLOOR3"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["vlan_id"] == 30
    assert body["subnet"] == "10.10.30.0/24"
    assert body["decisions"][0]["why"]
    assert body["upstream_config"].startswith("!")


def test_the_api_needs_a_name(client, upstream):
    res = client.post(f"{API}/devices/{upstream.id}/addition", json={"hostname": "  "})
    assert res.status_code == 422


def test_the_api_404s_for_an_unknown_upstream(client):
    res = client.post(f"{API}/devices/9999/addition", json={"hostname": "new-sw-01"})
    assert res.status_code == 404
