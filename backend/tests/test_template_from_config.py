"""いま動いている Config から、テンプレートを起こす。

同じ役割の機器の Config は、たいてい8割が同じ。その共通部分がその現場の
テンプレートになる。人が最初から書くより早く、少なくとも動く形にはなって
いる。
"""

import pytest

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import template_from_config
from tests.conftest import API


def _config(hostname: str, ip: str, vlan: int, extra: str = "") -> str:
    return f"""hostname {hostname}
!
vlan {vlan}
 name OFFICE
!
interface Vlan{vlan}
 ip address {ip} 255.255.255.0
 no shutdown
!
no ip http server
logging 10.0.0.9
enable secret 5 $1$abcd$xyz
{extra}!
line vty 0 15
 transport input ssh
"""


def _add(db, hostname: str, ip: str, vlan: int, digest: str, extra: str = "") -> Device:
    device = Device(hostname=hostname, vendor="Cisco", model="C9300")
    db.add(device)
    db.flush()
    db.add(
        ConfigSnapshot(
            device_id=device.id,
            hostname=hostname,
            filename=f"{hostname}.txt",
            content=_config(hostname, ip, vlan, extra),
            sha256=digest,
        )
    )
    db.flush()
    return device


@pytest.fixture()
def three(db_session):
    _add(db_session, "hq-sw-01", "10.10.10.2", 10, "a" * 64)
    _add(db_session, "hq-sw-02", "10.10.20.2", 20, "b" * 64)
    _add(db_session, "hq-sw-03", "10.10.30.2", 30, "c" * 64)
    db_session.commit()
    return db_session


def test_one_device_is_not_enough(db_session):
    """1台の Config はテンプレートではなく、その機器の設定でしかない。"""
    _add(db_session, "hq-sw-01", "10.10.10.2", 10, "a" * 64)
    db_session.commit()
    assert template_from_config.draft(db_session) is None


def test_the_common_lines_become_the_skeleton(three):
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    assert "no ip http server" in drafted.body
    assert "logging 10.0.0.9" in drafted.body
    assert "transport input ssh" in drafted.body


def test_what_differs_becomes_a_blank_to_fill(three):
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    names = {v["name"] for v in drafted.variables}
    assert "vlan_id" in names
    assert "ip_address" in names
    assert "{{ vlan_id }}" in drafted.body
    assert "{{ ip_address }}" in drafted.body


def test_what_is_the_same_everywhere_stays_as_it_is(three):
    """変数にすると、入れる人が毎回同じ値を打つことになる。"""
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    assert "logging 10.0.0.9" in drafted.body
    assert not any(v["default"] == "10.0.0.9" for v in drafted.variables)


def test_an_interface_named_by_its_number_stays_on_its_line(three):
    """行が落ちると、その下にぶら下がる行だけが残って貼れない形になる。"""
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    body = drafted.body
    assert "interface {{ interface }}" in body
    assert body.index("interface {{ interface }}") < body.index(" ip address")


def test_a_repeated_separator_is_not_collapsed(three):
    """`!` が1つに畳まれると、区切りの無い Config になる。"""
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    assert drafted.body.count("!") >= 4


def test_secrets_never_reach_the_template(three):
    """見せられないものは持ち回れない。"""
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    assert "enable secret" not in drafted.body
    assert drafted.skipped


def test_a_line_only_one_device_has_is_left_out(db_session):
    """1台だけの事情をテンプレートに持ち込まない。"""
    _add(db_session, "hq-sw-01", "10.10.10.2", 10, "a" * 64, extra="ip domain-name only.example\n")
    _add(db_session, "hq-sw-02", "10.10.20.2", 20, "b" * 64)
    _add(db_session, "hq-sw-03", "10.10.30.2", 30, "c" * 64)
    db_session.commit()

    drafted = template_from_config.draft(db_session, hostname_prefix="hq-")
    assert "only.example" not in drafted.body


def test_it_says_what_it_was_built_from(three):
    """「この書式どこ見て書いたの？」に答えられないと保守できない。"""
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    assert drafted.devices_seen == 3
    assert drafted.hostnames == ["hq-sw-01", "hq-sw-02", "hq-sw-03"]
    assert any("3 台" in note for note in drafted.notes)


def test_the_prefix_narrows_it_to_devices_of_the_same_role(db_session):
    _add(db_session, "hq-sw-01", "10.10.10.2", 10, "a" * 64)
    _add(db_session, "hq-sw-02", "10.10.20.2", 20, "b" * 64)
    _add(db_session, "br-rt-01", "10.20.10.2", 30, "c" * 64, extra="router bgp 65001\n")
    db_session.commit()

    drafted = template_from_config.draft(db_session, hostname_prefix="hq-")
    assert drafted.devices_seen == 2
    assert "router bgp" not in drafted.body


def test_it_can_be_saved_as_a_template(three):
    drafted = template_from_config.draft(three, hostname_prefix="hq-")
    template = template_from_config.save(three, drafted, name="拠点スイッチ標準")
    assert template.id is not None
    assert template.name == "拠点スイッチ標準"
    assert "いま動いている Config" in template.description
    assert template.variables


def test_the_api_drafts_and_saves(client, three):
    res = client.post(f"{API}/config-templates/from-config", json={"hostname_prefix": "hq-"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["devices_seen"] == 3
    assert body["variables"]
    assert body["notes"]

    saved = client.post(
        f"{API}/config-templates/from-config/save",
        json={"hostname_prefix": "hq-", "name": "拠点スイッチ標準"},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["name"] == "拠点スイッチ標準"


def test_the_api_says_when_there_is_not_enough_to_work_with(client, db_session):
    _add(db_session, "hq-sw-01", "10.10.10.2", 10, "a" * 64)
    db_session.commit()
    res = client.post(f"{API}/config-templates/from-config", json={})
    assert res.status_code == 422
    assert "2台" in res.json()["detail"]
