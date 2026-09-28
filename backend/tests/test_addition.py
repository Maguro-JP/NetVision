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


def test_the_config_carries_commands_only(db_session, upstream):
    """貼ればそのまま通る形にする。

    説明や「要記入」の断り書きを行に混ぜると、貼る前に消す手間が要るうえ、
    消し忘れがそのまま機器に入る。伝えたいことは注記として別に返す。
    """
    plan = _plan(db_session, upstream)
    for body in (plan.upstream_config, plan.device_config):
        for line in body.splitlines():
            stripped = line.strip()
            if stripped in {"!", "#", ""}:
                continue  # 区切りは Config の書式そのもの
            assert not stripped.startswith(("!", "#")), line
            assert addition.TODO not in line


def test_the_minimum_that_connects_is_said_in_the_notes(db_session, upstream):
    """急ぐときはどこまで入れれば繋がるのかを、Config の外で伝える。"""
    plan = _plan(db_session, upstream)
    assert any("入れれば繋がります" in note for note in plan.device_notes)
    assert any("入れれば繋がります" in note for note in plan.upstream_notes)


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
    # 既存から読み取れない値は Config に入れず、注記で伝える
    assert any("SNMP" in note for note in plan.device_notes)


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
    """それらしい別ベンダーの構文を出すと、そのまま貼られて事故になる。"""
    plan = _plan(db_session, upstream, vendor="Juniper")
    assert plan.device_config == ""
    assert any("起こしていません" in note for note in plan.device_notes)
    assert any("10.10.30.2" in note for note in plan.device_notes)


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
    assert body["upstream_config"].startswith("vlan 30")
    assert body["device_notes"]


def test_the_api_needs_a_name(client, upstream):
    res = client.post(f"{API}/devices/{upstream.id}/addition", json={"hostname": "  "})
    assert res.status_code == 422


def test_the_api_404s_for_an_unknown_upstream(client):
    res = client.post(f"{API}/devices/9999/addition", json={"hostname": "new-sw-01"})
    assert res.status_code == 404


def test_link_local_and_documentation_ranges_are_not_used_as_a_basis():
    """169.254/16 や 192.0.2/24 を土台にすると、使えない帯を勧めてしまう。

    ipaddress の is_private はこれらも真になるので、社内で自由に採番できる
    帯（RFC1918）だけを見る。土台が無いなら、当てずっぽうを出さない。
    """
    assert pick_subnet(["169.254.0.0/24"], 30) is None
    assert pick_subnet(["203.0.113.0/24"], 30) is None
    assert pick_subnet(["8.8.8.0/24"], 30) is None
    # RFC1918 の3つは、これまで通り土台になる
    assert pick_subnet(["192.168.1.0/24"], 10) == "192.168.10.0/24"
    assert pick_subnet(["172.16.5.0/24"], 7) == "172.16.7.0/24"


def test_the_main_run_of_segments_decides_the_basis():
    """1本だけ別の帯があっても、そちらに引きずられない。

    10.10.<VLAN>.0/24 で採番している現場に 10.0.0.0/24 が1本あると、
    番号の小さいほうを土台にしていたため 10.0.<VLAN>.0/24 を勧めていた。
    提案だけが現場の流儀から外れる。
    """
    used = ["10.10.10.0/24", "10.10.20.0/24", "10.0.0.0/24", "10.10.40.0/24"]
    assert pick_subnet(used, 2) == "10.10.2.0/24"
    assert pick_subnet(used, 30) == "10.10.30.0/24"
    # 数が同じなら、番号の小さいほうを採る（毎回同じ答えになるように）
    assert pick_subnet(["10.10.10.0/24", "10.20.10.0/24"], 30) == "10.10.30.0/24"


def test_the_latest_config_of_every_device_is_found_in_one_query(db_session):
    """全台を見る確認（STP・HA）は、機器ごとに問い合わせず、最新の Config の id をまとめて引く。"""
    import hashlib

    from app.models import Device
    from app.models.topology import ConfigSnapshot, ConfigSource
    from app.services.addition import latest_config_ids

    a = Device(hostname="a", vendor="Cisco", model="m")
    b = Device(hostname="b", vendor="Cisco", model="m")
    c = Device(hostname="c", vendor="Cisco", model="m")
    db_session.add_all([a, b, c])
    db_session.flush()
    snapshots = []
    for device, body in [(a, "old"), (a, "new"), (b, "only")]:
        snapshot = ConfigSnapshot(
            device_id=device.id,
            hostname=device.hostname,
            filename="c.txt",
            content=body,
            sha256=hashlib.sha256(body.encode()).hexdigest(),
            source=ConfigSource.UPLOAD,
        )
        db_session.add(snapshot)
        db_session.flush()
        snapshots.append(snapshot)
    assert latest_config_ids(db_session) == {a.id: snapshots[1].id, b.id: snapshots[2].id}
