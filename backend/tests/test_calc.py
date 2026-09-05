"""数えれば分かることを、数えて出す。

ここは推測しない側の処理なので、答えが一つに決まることだけを確かめる。
決まらないもの（速度が書いていない束など）は、断定しないことを確かめる。
"""

from app.models import Device
from app.models.topology import Interface, Link
from app.models.vlan import DeviceVlan, Vlan
from app.services import calc
from tests.conftest import API


def _device(db, hostname: str, *ports: dict) -> Device:
    device = Device(hostname=hostname, vendor="Cisco", model="C9300-48P")
    db.add(device)
    db.flush()
    for values in ports:
        name = values.pop("name")
        db.add(Interface(device_id=device.id, name=name, normalized_name=name.lower(), **values))
    db.flush()
    return device


class TestCounting:
    def test_a_slash_24_holds_254(self):
        """先頭と最後は機器に付けられないので、256 ではなく 254。"""
        assert calc.usable_hosts("10.0.0.0/24") == 254

    def test_a_slash_30_holds_two(self):
        assert calc.usable_hosts("10.0.0.0/30") == 2

    def test_a_slash_31_holds_two(self):
        """対向2台むけの書き方。ここだけは引き算をしない。"""
        assert calc.usable_hosts("10.0.0.0/31") == 2

    def test_nonsense_is_not_guessed(self):
        assert calc.usable_hosts("えいご") is None

    def test_ratio_has_one_decimal(self):
        assert calc.usage_ratio(127, 254) == 50.0
        assert calc.usage_ratio(1, 3) == 33.3

    def test_ratio_without_capacity_is_zero(self):
        assert calc.usage_ratio(5, 0) == 0.0


class TestVlanNumbers:
    def test_reserved_numbers_are_skipped(self):
        used = set(range(2, 1002))
        assert 1002 not in calc.free_vlan_ids(used, want=3)

    def test_used_numbers_are_skipped(self):
        assert calc.free_vlan_ids({2, 3, 4}, want=2) == [5, 6]

    def test_vlan_one_is_never_offered(self):
        """初期値として全ポートに付いている番号を、新規に採らせない。"""
        assert 1 not in calc.free_vlan_ids(set(), want=5)


class TestSpeed:
    def test_plain_numbers_are_megabits(self):
        assert calc.speed_mbps("1000") == 1000

    def test_giga_is_expanded(self):
        assert calc.speed_mbps("10G") == 10000

    def test_auto_is_not_a_number(self):
        """相手と決める設定なので、数として扱わない。"""
        assert calc.speed_mbps("auto") is None
        assert calc.speed_mbps(None) is None


class TestFromLedger:
    def test_subnet_usage_says_how_many_more_fit(self, db_session):
        _device(db_session, "core-01", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
        _device(db_session, "core-02", {"name": "Vlan10", "ip_address": "10.0.10.2/24"})
        db_session.add(Vlan(vlan_id=10, name="USERS", subnet="10.0.10.0/24"))
        db_session.commit()

        found = calc.subnet_usage(db_session)
        assert len(found) == 1
        assert found[0].capacity == 254
        assert found[0].used == 2
        assert found[0].free == 252
        assert found[0].vlan_name == "USERS"
        assert "254" in found[0].basis

    def test_a_full_segment_is_flagged(self, db_session):
        ports = [{"name": f"Vlan{i}", "ip_address": f"10.0.0.{i}/29"} for i in range(1, 6)]
        _device(db_session, "core-01", *ports)
        db_session.commit()

        found = calc.subnet_usage(db_session)
        assert found[0].crowded is True

    def test_numbers_used_only_on_the_equipment_are_still_used(self, db_session):
        """台帳に無いから空き、として採らせない。"""
        device = _device(db_session, "core-01", {"name": "Vlan20", "vlan": 20})
        db_session.add(DeviceVlan(device_id=device.id, vlan_id=30, name="SRV"))
        db_session.add(Vlan(vlan_id=40))
        db_session.commit()

        numbers = calc.vlan_numbers(db_session)
        assert numbers.used == [20, 30, 40]
        assert 20 not in numbers.free_next
        assert 30 not in numbers.free_next

    def test_port_usage_counts_only_physical_ports(self, db_session):
        _device(
            db_session,
            "acc-01",
            {"name": "GigabitEthernet1/0/1", "description": "AP"},
            {"name": "GigabitEthernet1/0/2"},
            {"name": "Vlan10", "ip_address": "10.0.10.1/24"},
        )
        db_session.commit()

        found = calc.port_usage(db_session)
        assert found[0].total == 2
        assert found[0].in_use == 1
        assert found[0].free == 1

    def test_a_linked_port_counts_as_used(self, db_session):
        a = _device(db_session, "acc-01", {"name": "GigabitEthernet1/0/1"})
        b = _device(db_session, "core-01", {"name": "GigabitEthernet1/0/1"})
        db_session.add(
            Link(
                a_device_id=a.id,
                a_interface="GigabitEthernet1/0/1",
                b_device_id=b.id,
                b_interface="GigabitEthernet1/0/1",
            )
        )
        db_session.commit()

        found = {p.hostname: p for p in calc.port_usage(db_session)}
        assert found["acc-01"].in_use == 1
        assert found["acc-01"].free == 0

    def test_a_bundle_says_what_is_left_when_one_dies(self, db_session):
        _device(
            db_session,
            "core-01",
            {"name": "GigabitEthernet1/0/1", "lag_group": 1, "speed": "1000"},
            {"name": "GigabitEthernet1/0/2", "lag_group": 1, "speed": "1000"},
        )
        db_session.commit()

        found = calc.lag_usage(db_session)
        assert found[0].members == 2
        assert found[0].total_mbps == 2000
        assert found[0].remaining_mbps == 1000

    def test_a_bundle_without_speeds_is_not_guessed(self, db_session):
        _device(
            db_session,
            "core-01",
            {"name": "GigabitEthernet1/0/1", "lag_group": 2},
            {"name": "GigabitEthernet1/0/2", "lag_group": 2},
        )
        db_session.commit()

        found = calc.lag_usage(db_session)
        assert found[0].total_mbps is None
        assert "本数だけ" in found[0].note

    def test_the_same_address_in_two_places_is_surfaced(self, db_session):
        _device(db_session, "core-01", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
        _device(db_session, "core-02", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
        db_session.commit()

        found = calc.duplicate_addresses(calc._interface_rows(db_session))
        assert len(found) == 1
        assert found[0].address == "10.0.10.1"
        assert len(found[0].places) == 2

    def test_an_empty_ledger_says_so_instead_of_showing_zeros(self, db_session):
        found = calc.report(db_session)
        assert found.subnets == []
        assert any("Config" in note for note in found.notes)


def test_the_api_returns_what_it_counted(db_session, client):
    """画面から引ける形になっていること。"""
    _device(db_session, "core-01", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
    db_session.commit()

    response = client.get(f"{API}/calc")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subnets"][0]["subnet"] == "10.0.10.0/24"
    assert body["subnets"][0]["free"] == 253
    assert body["vlans"]["free_next"]


def test_it_says_how_many_devices_the_count_is_based_on(db_session):
    """残り何台入るかは、採番を決める人がそのまま使う数字である。

    読めた範囲を言わないと、台帳全体を数えたものだと受け取られる。
    """
    _device(db_session, "counted-sw-01", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
    # ポートが1つも読めていない機器（台帳にはある）
    db_session.add(Device(hostname="dark-sw-01", vendor="Cisco", model="C9300-48P"))
    db_session.commit()

    report = calc.report(db_session)
    assert any("2 台のうち Config が入っている 1 台" in note for note in report.notes)


def test_nothing_is_said_when_every_device_was_counted(db_session):
    _device(db_session, "all-sw-01", {"name": "Vlan10", "ip_address": "10.0.10.1/24"})
    db_session.commit()

    report = calc.report(db_session)
    assert not any("この数に入っていません" in note for note in report.notes)
