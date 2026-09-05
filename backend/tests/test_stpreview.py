"""木の根（STP のルート）が、どこに立つかを機器をまたいで見る。

1台の Config だけでは言えない。優先度は相手と比べてはじめて意味を持つ。
"""

from app.models import Device
from app.models.topology import ConfigSnapshot
from app.services import stpreview
from tests.conftest import API

CORE = """hostname core-sw-01
spanning-tree mode rapid-pvst
spanning-tree vlan 10,20 priority 4096
interface GigabitEthernet1/0/1
 switchport mode trunk
"""

ACCESS = """hostname acc-sw-01
spanning-tree mode rapid-pvst
interface GigabitEthernet1/0/1
 switchport mode trunk
"""


def _device(db, hostname: str, text: str, device_type: str = "switch") -> Device:
    device = Device(hostname=hostname, vendor="Cisco", device_type=device_type)
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


def test_nobody_deciding_the_root_is_pointed_out(db_session):
    _device(db_session, "acc-sw-01", ACCESS)
    _device(db_session, "acc-sw-02", ACCESS.replace("acc-sw-01", "acc-sw-02"))

    report = stpreview.review(db_session)
    assert report.devices_seen == 2
    assert [f.title for f in report.findings if "誰も決めていません" in f.title]


def test_the_device_with_the_lowest_priority_is_the_root(db_session):
    _device(db_session, "core-sw-01", CORE)
    _device(db_session, "acc-sw-01", ACCESS)

    report = stpreview.review(db_session)
    assert report.roots[10] == "core-sw-01"
    assert report.roots[20] == "core-sw-01"
    assert not [f.title for f in report.findings if "誰も決めていません" in f.title]


def test_a_root_that_changes_by_vlan_is_pointed_out(db_session):
    _device(db_session, "core-sw-01", CORE)
    other = CORE.replace("core-sw-01", "core-sw-02").replace(
        "spanning-tree vlan 10,20 priority 4096", "spanning-tree vlan 20 priority 0"
    )
    _device(db_session, "core-sw-02", other)

    report = stpreview.review(db_session)
    assert report.roots[10] == "core-sw-01"
    assert report.roots[20] == "core-sw-02"
    assert [f.title for f in report.findings if "VLAN によって" in f.title]


def test_a_single_root_without_a_backup_is_pointed_out(db_session):
    _device(db_session, "core-sw-01", CORE)
    _device(db_session, "acc-sw-01", ACCESS)

    report = stpreview.review(db_session)
    assert [f.title for f in report.findings if "次にどこへ移るか" in f.title]


def test_two_devices_with_priorities_need_no_backup_warning(db_session):
    _device(db_session, "core-sw-01", CORE)
    second = CORE.replace("core-sw-01", "core-sw-02").replace("priority 4096", "priority 8192")
    _device(db_session, "core-sw-02", second)

    report = stpreview.review(db_session)
    assert not [f.title for f in report.findings if "次にどこへ移るか" in f.title]


def test_the_aruba_step_is_read_as_the_real_value(db_session):
    """Aruba CX は 0〜15 の段で書く。4096 を掛けた値で比べる。"""
    aruba = """!Version ArubaOS-CX FL.10.09.0010
hostname sw-aruba-01
spanning-tree mode mstp
spanning-tree priority 1
"""
    _device(db_session, "sw-aruba-01", aruba)
    _device(db_session, "core-sw-01", CORE)

    report = stpreview.review(db_session)
    # 段の 1 は 4096。Cisco 側の 4096 と並ぶので、根は決まらない
    assert report.roots[10] == "決まりません"


def test_different_modes_are_pointed_out(db_session):
    _device(db_session, "core-sw-01", CORE)
    _device(db_session, "acc-sw-01", ACCESS.replace("rapid-pvst", "mst"))

    report = stpreview.review(db_session)
    assert [f.title for f in report.findings if "モードがそろっていません" in f.title]


def test_a_config_without_stp_is_left_out(db_session):
    _device(db_session, "rt-01", "hostname rt-01\ninterface GigabitEthernet0/0\n")

    report = stpreview.review(db_session)
    assert report.devices_seen == 0
    assert report.notes


def test_the_api_returns_the_report(client, db_session):
    _device(db_session, "core-sw-01", CORE)
    _device(db_session, "acc-sw-01", ACCESS)

    body = client.get(f"{API}/topology/stp-check").json()
    assert body["devices_seen"] == 2
    assert body["roots"]["10"] == "core-sw-01"
    assert all(item["why"] for item in body["findings"])


def test_it_says_how_many_switches_it_could_not_look_at(db_session):
    """見ていない機器があるのに指摘が0件だと、全部そろっていると読まれる。

    STP は輪が1つできるだけで拠点ごと止まる。範囲を言わずに黙らない。
    """
    _device(db_session, "root-sw-01", CORE)
    _device(db_session, "root-sw-02", CORE.replace("core-sw-01", "root-sw-02"))
    # STP の設定が読めないスイッチ（Config が無い）
    db_session.add(Device(hostname="dark-sw-01", vendor="Cisco", device_type="switch"))
    db_session.commit()

    report = stpreview.review(db_session)
    assert report.devices_seen == 2
    assert any("3 台のうち 2 台" in note for note in report.notes)


def test_nothing_is_said_when_every_switch_was_looked_at(db_session):
    _device(db_session, "seen-sw-01", CORE)
    _device(db_session, "seen-sw-02", CORE.replace("core-sw-01", "seen-sw-02"))

    report = stpreview.review(db_session)
    assert not any("見ていません" in note for note in report.notes)
