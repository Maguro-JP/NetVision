"""条件から構成を起こす。

「A と B を、この条件で繋ぐならどうなるか」に答える。間に何を挟むかは
条件から機械的に決まるので、そこを決めてしまう。決めたら理由を残す。
"""

from app.models import Device, DeviceType, Site
from app.models.topology import Link
from app.services import blueprint as bp
from tests.conftest import API


def _plan(*conditions, **overrides):
    return bp.build(
        bp.BlueprintRequest(
            a_name=overrides.pop("a_name", "honsha"),
            b_name=overrides.pop("b_name", "shisha"),
            conditions=list(conditions),
            prefix=overrides.pop("prefix", "test"),
            **overrides,
        )
    )


def test_nothing_extra_is_placed_when_nothing_requires_it():
    """挟むほど止まる場所が増える。条件が要求する分だけ置く。"""
    plan = _plan()
    assert [d.device_type for d in plan.devices] == [DeviceType.SWITCH, DeviceType.SWITCH]
    assert len(plan.links) == 1
    assert len(plan.segments) == 1


def test_separating_the_segments_brings_a_router():
    plan = _plan(bp.SEPARATE)
    assert any("rt" in name for name in plan.configs)
    assert len(plan.segments) == 2


def test_restricting_traffic_brings_a_firewall_with_a_reason():
    plan = _plan(bp.RESTRICT)
    assert any("fw" in name for name in plan.configs)
    decision = next(d for d in plan.decisions if d.topic == "ファイアウォール")
    assert "絞る" in decision.why


def test_the_firewall_sits_in_front_of_what_it_protects():
    """奥に置くと、通ってから止めることになる。"""
    plan = _plan(bp.SEPARATE, bp.RESTRICT)
    first_link = plan.links[0]
    assert "fw" in first_link.b_hostname


def test_each_port_is_used_once():
    """同じポートを2つの線に割り当てると、貼った時点で上書きされる。"""
    plan = _plan(bp.SEPARATE, bp.RESTRICT, bp.REDUNDANT)
    used = [(port.hostname, port.interface) for port in plan.ports]
    assert len(used) == len(set(used))


def test_both_ends_of_a_link_between_devices_get_their_own_address():
    plan = _plan(bp.SEPARATE, bp.RESTRICT)
    transit = next(s for s in plan.segments if s.vlan_id is None)
    addresses = [a.address for a in plan.addresses if a.segment == transit.name]
    assert len(addresses) == 2
    assert len(set(addresses)) == 2


def test_a_middle_device_learns_how_to_reach_the_far_side():
    """隣までは直結でも、その先へは行き方を教えないと届かない。"""
    plan = _plan(bp.SEPARATE, bp.RESTRICT)
    assert plan.routes
    for route in plan.routes:
        assert route.next_hop in {a.address for a in plan.addresses}


def test_redundancy_gives_a_virtual_ip_and_different_priorities():
    """同じ優先度だと、どちらが主になるかが起動の順番で決まる。"""
    plan = _plan(bp.SEPARATE, bp.REDUNDANT)
    edge = next(s for s in plan.segments if s.vlan_id is not None)
    entries = [a for a in plan.addresses if a.segment == edge.name]
    assert len(entries) == 2
    assert {e.virtual_ip for e in entries} == {edge.gateway}
    assert entries[0].priority != entries[1].priority


def test_redundancy_without_a_middle_device_is_called_out_not_faked():
    plan = _plan(bp.REDUNDANT)
    assert any("線が切れたら通りません" in w for w in plan.warnings)


def test_the_switch_side_of_a_routed_port_is_an_access_port():
    """相手が L3 のポートなら、タグを付けずに渡す。"""
    plan = _plan(bp.SEPARATE)
    switch_config = plan.configs[next(n for n in plan.configs if "honsha" in n)]
    assert "switchport mode access" in switch_config
    assert "switchport mode trunk" not in switch_config


def test_two_switches_facing_each_other_stay_a_trunk():
    plan = _plan()
    switch_config = plan.configs[next(n for n in plan.configs if "honsha" in n)]
    assert "switchport mode trunk" in switch_config


def test_a_router_gets_no_vlan_definitions():
    plan = _plan(bp.SEPARATE)
    router_config = plan.configs[next(n for n in plan.configs if "rt" in n)]
    assert "vlan " not in router_config


def test_the_config_carries_commands_only():
    plan = _plan(bp.SEPARATE, bp.RESTRICT)
    for body in plan.configs.values():
        for line in body.splitlines():
            stripped = line.strip()
            if stripped in {"!", "#", ""}:
                continue
            assert not stripped.startswith(("!", "#")), line


def test_what_was_left_out_is_said_in_the_notes():
    plan = _plan(bp.RESTRICT)
    firewall = next(n for n in plan.notes if "fw" in n)
    assert any("通す通信の中身" in note for note in plan.notes[firewall])


def test_a_too_narrow_address_range_falls_back_and_says_so():
    plan = _plan(bp.SEPARATE, base="10.0.0.0/29")
    assert any("狭すぎる" in w for w in plan.warnings)
    assert plan.segments[0].subnet.startswith("10.90.")


def test_names_stay_ascii():
    """機器名に日本語を入れると、機器によっては化ける。"""
    plan = _plan(bp.SEPARATE, a_name="本社", b_name="支社")
    for hostname in plan.configs:
        assert hostname.isascii()


# ---------------------------------------------------------------------------
# テスト拠点として置いてみる
# ---------------------------------------------------------------------------


def test_it_lands_in_the_ledger_as_a_marked_test_site(db_session):
    plan = _plan(bp.SEPARATE, bp.RESTRICT)
    site = bp.apply_to_ledger(db_session, plan, "honsha-shisha")
    assert site.name.startswith(bp.TEST_PREFIX)
    devices = db_session.query(Device).filter(Device.site_id == site.id).all()
    assert len(devices) == len(plan.devices)
    assert db_session.query(Link).count() == len(plan.links)


def test_the_generated_config_is_kept_with_the_device(db_session):
    """図から機器を開けば中身が見える。案はそのほうが確かめやすい。"""
    from app.models.topology import ConfigSnapshot

    plan = _plan(bp.SEPARATE)
    bp.apply_to_ledger(db_session, plan, "honsha-shisha")
    assert db_session.query(ConfigSnapshot).count() == len(plan.devices)


def test_a_test_site_can_be_removed_whole(db_session):
    plan = _plan(bp.SEPARATE)
    site = bp.apply_to_ledger(db_session, plan, "honsha-shisha")
    removed = bp.remove_test_site(db_session, site.id)
    assert removed == len(plan.devices)
    assert db_session.query(Device).count() == 0
    assert db_session.query(Link).count() == 0


def test_a_real_site_cannot_be_removed_through_this_door(db_session):
    """取り違えたときに取り返しがつかない。印の付いた拠点だけ消せる。"""
    site = Site(name="本社")
    db_session.add(site)
    db_session.commit()
    try:
        bp.remove_test_site(db_session, site.id)
    except ValueError as error:
        assert "テスト用" in str(error)
    else:
        raise AssertionError("本物の拠点が消せてしまった")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_the_api_lists_the_conditions(client):
    body = client.get(f"{API}/blueprint/conditions").json()
    assert {c["name"] for c in body} == set(bp.CONDITION_LABELS)


def test_the_api_draws_without_touching_the_ledger(client, db_session):
    res = client.post(
        f"{API}/blueprint",
        json={"a_name": "honsha", "b_name": "shisha", "conditions": [bp.SEPARATE]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["decisions"]
    assert db_session.query(Device).count() == 0


def test_the_api_refuses_a_condition_it_does_not_know(client):
    res = client.post(f"{API}/blueprint", json={"conditions": ["なんでもする"]})
    assert res.status_code == 422


def test_the_api_can_place_and_remove_the_test_site(client):
    res = client.post(
        f"{API}/blueprint/apply",
        json={"a_name": "honsha", "b_name": "shisha", "conditions": [bp.SEPARATE]},
    )
    assert res.status_code == 200, res.text
    site_id = res.json()["site_id"]

    listed = client.get(f"{API}/blueprint/test-sites").json()
    assert [s["id"] for s in listed] == [site_id]

    removed = client.delete(f"{API}/blueprint/test-sites/{site_id}")
    assert removed.status_code == 200
    assert client.get(f"{API}/blueprint/test-sites").json() == []


def test_aruba_cx_configs_read_back_with_the_same_tool():
    """AOS-CX の書式で出したものを、同じ道具の読み手で読み戻せること。"""
    from app.services import configparse
    from app.services.configparse import Vendor

    plan = _plan(bp.SEPARATE, bp.REDUNDANT, vendor="Aruba")
    for hostname, body in plan.configs.items():
        parsed = configparse.parse(body)
        assert parsed.vendor is Vendor.ARUBA_CX, hostname
        assert parsed.hostname == hostname
        # ポートは AOS-CX の形で、全部 no shutdown を書いてある
        assert all(i.name.startswith("1/1/") for i in parsed.interfaces), hostname
        assert all(i.enabled for i in parsed.interfaces), hostname
    routed = [p for p in plan.ports if p.routed]
    assert routed, "ルーターが挟まっていれば L3 のポートがある"
    body = plan.configs[routed[0].hostname]
    assert "vrrp" in body
    assert "routing" in body


def test_arista_and_allied_configs_read_back_with_the_same_tool():
    from app.services import configparse
    from app.services.configparse import Vendor

    for vendor, expected, prefix in (
        ("Arista", Vendor.ARISTA, "Ethernet"),
        ("Allied", Vendor.ALLIED, "port1.0."),
    ):
        plan = _plan(bp.SEPARATE, bp.REDUNDANT, vendor=vendor)
        for hostname, body in plan.configs.items():
            parsed = configparse.parse(body)
            assert parsed.vendor is expected, (vendor, hostname)
            assert parsed.hostname == hostname
            assert all(i.name.startswith(prefix) for i in parsed.interfaces), (vendor, hostname)
        router = next(p.hostname for p in plan.ports if p.routed)
        parsed = configparse.parse(plan.configs[router])
        # 経路は長さ付きで読めている
        assert any("/" in r.destination for r in parsed.routes) or not plan.routes
        assert any(i.ip_address and "/" in i.ip_address for i in parsed.interfaces)


def test_an_unwritten_vendor_gets_the_decisions_not_cisco_syntax():
    """書式を書いていないベンダーで、Cisco の構文を出さないこと。

    そのまま貼れるように見えて入らない。もっともらしい嘘の Config は、
    空欄より害が大きい。
    """
    plan = _plan(bp.SEPARATE, bp.REDUNDANT, vendor="Extreme")
    bodies = "\n".join(plan.configs.values())
    assert "未対応" in bodies
    assert "switchport" not in bodies
    assert "hostname " not in bodies
    # 決めた中身そのものは出す。空にはしない
    for hostname in plan.configs:
        assert hostname in plan.configs[hostname]


def test_junos_configs_read_back_with_the_same_tool():
    """set 形式で出したものを、同じ道具の読み手で読み戻せること。"""
    from app.services import configparse
    from app.services.configparse import Vendor

    plan = _plan(bp.SEPARATE, bp.REDUNDANT, vendor="Juniper")
    for hostname, body in plan.configs.items():
        parsed = configparse.parse(body)
        assert parsed.vendor is Vendor.JUNIPER, hostname
        assert parsed.hostname == hostname
        assert all(item.name.startswith("ge-0/0/") for item in parsed.interfaces), hostname
    router = next(p.hostname for p in plan.ports if p.routed)
    body = plan.configs[router]
    assert "family inet address" in body
    assert "vrrp-group" in body
    # 入り口は ssh だけにする
    assert "delete system services telnet" in body


def test_alaxala_configs_read_back_with_the_same_tool():
    """AX の書式で出したものを、同じ道具の読み手で読み戻せること。"""
    from app.services import configparse
    from app.services.configparse import Vendor

    plan = _plan(bp.SEPARATE, bp.REDUNDANT, vendor="アラクサラ")
    for hostname, body in plan.configs.items():
        parsed = configparse.parse(body)
        assert parsed.vendor is Vendor.ALAXALA, hostname
        assert parsed.hostname == hostname
        assert parsed.interfaces, hostname
    router = next(p.hostname for p in plan.ports if p.routed)
    body = plan.configs[router]
    assert "vrrp" in body
    # 口の種別と番号は分けて書く
    assert "interface gigabitethernet " in body or "interface vlan " in body
