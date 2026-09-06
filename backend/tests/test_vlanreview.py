"""VLAN の名前が、機器と台帳でそろっているか。

番号は同じでもつながるので、名前の食い違いは通信としては出てこない。
出てくるのは人の側で、「10 番を止めて」が別の機器で別の意味になる。
1台ずつ見ても分からないので、番号ごとに機器をまたいで見る。
"""

from app.models import Device, DeviceType
from app.models.vlan import DeviceVlan, Vlan
from app.services import vlanreview
from app.services.reviewnote import LOW, MEDIUM


def _device(db, hostname: str) -> Device:
    device = Device(hostname=hostname, vendor="Cisco", model="C9300")
    db.add(device)
    db.flush()
    return device


def _vlan(db, device: Device, vlan_id: int, name: str | None) -> None:
    db.add(DeviceVlan(device_id=device.id, vlan_id=vlan_id, name=name))


def _titles(db) -> list[str]:
    return [item.finding.title for item in vlanreview.review(db).findings]


def test_nothing_to_say_without_any_config(db_session):
    report = vlanreview.review(db_session)
    assert report.findings == []
    assert report.notes  # 何も無いときは、何が要るのかを言う


def test_the_same_name_everywhere_is_quiet(db_session):
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 10, "USERS")
    _vlan(db_session, b, 10, "USERS")
    db_session.add(Vlan(vlan_id=10, name="USERS"))
    db_session.commit()
    assert _titles(db_session) == []


def test_only_the_way_of_writing_differs_is_quiet(db_session):
    """USER-SEG と USER_SEG は同じもの。ここを厳密にすると読めなくなる。"""
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 10, "USER-SEG")
    _vlan(db_session, b, 10, "user_seg")
    db_session.add(Vlan(vlan_id=10, name="USER SEG"))
    db_session.commit()
    assert _titles(db_session) == []


def test_different_names_on_the_same_number_are_reported(db_session):
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 10, "SRV")
    _vlan(db_session, b, 10, "業務")
    db_session.commit()
    report = vlanreview.review(db_session)
    assert report.findings[0].finding.title == "VLAN 10 の名前が、機器によって違います"
    assert report.findings[0].finding.level == MEDIUM
    # どの機器がどう書いているかまで出す。出さないと直しに行けない
    assert any("sw-a: SRV" in line for line in report.findings[0].finding.evidence)


def test_a_name_that_differs_from_the_ledger_is_reported(db_session):
    a = _device(db_session, "sw-a")
    _vlan(db_session, a, 20, "OLD-NAME")
    db_session.add(Vlan(vlan_id=20, name="SERVERS"))
    db_session.commit()
    found = next(item for item in vlanreview.review(db_session).findings if item.vlan_id == 20)
    assert found.finding.title == "VLAN 20 は、台帳と機器で名前が違います"
    assert found.finding.level == LOW
    assert "台帳: SERVERS" in found.finding.evidence


def test_a_number_used_on_many_devices_but_missing_from_the_ledger_is_reported(db_session):
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 30, "MGMT")
    _vlan(db_session, b, 30, "MGMT")
    db_session.commit()
    assert "VLAN 30 が台帳にありません（2台に入っています）" in _titles(db_session)


def test_a_number_on_a_single_device_is_not_chased(db_session):
    """1台にしか無いものは、試したまま残っている可能性がある。並べても仕方ない。"""
    a = _device(db_session, "sw-a")
    _vlan(db_session, a, 40, "TEST")
    db_session.commit()
    assert _titles(db_session) == []


def test_the_report_counts_what_it_looked_at(db_session):
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 10, "USERS")
    _vlan(db_session, b, 10, "USERS")
    _vlan(db_session, b, 20, "SERVERS")
    db_session.add(Vlan(vlan_id=10, name="USERS"))
    db_session.commit()
    report = vlanreview.review(db_session)
    assert (report.vlans_seen, report.devices_with_vlans) == (2, 2)


def test_the_api_returns_the_same_thing(client, db_session):
    a, b = _device(db_session, "sw-a"), _device(db_session, "sw-b")
    _vlan(db_session, a, 10, "SRV")
    _vlan(db_session, b, 10, "業務")
    db_session.commit()
    body = client.get("/api/v1/topology/vlan-check").json()
    assert body["vlans_seen"] == 1
    assert body["findings"][0]["vlan_id"] == 10


def _svi(db, device: Device, vlan_id: int, address: str) -> None:
    from app.models.topology import Interface

    db.add(
        Interface(
            device_id=device.id,
            name=f"Vlan{vlan_id}",
            normalized_name=f"vlan{vlan_id}",
            ip_address=address,
        )
    )
    db.flush()


def test_a_ledger_subnet_that_does_not_hold_the_real_address_is_called_out(db_session):
    """台帳の区画と実機のアドレスが違うのは、名前の食い違いより重い。

    次に番号を決める人は台帳の区画から住所を選ぶ。そこが実機と違うと、
    選んだ住所はそのセグメントに載らない。
    """
    device = _device(db_session, "sw-01")
    _vlan(db_session, device, 30, "MGMT")
    _svi(db_session, device, 30, "10.0.0.11/24")
    db_session.add(Vlan(vlan_id=30, name="MGMT", subnet="192.168.30.0/24"))
    db_session.flush()

    item = next(i for i in vlanreview.review(db_session).findings if "区画" in i.finding.title)
    assert item.finding.level == MEDIUM
    assert "台帳: 192.168.30.0/24" in item.finding.evidence
    assert any("10.0.0.11/24" in line for line in item.finding.evidence)


def test_a_wider_ledger_subnet_that_holds_the_address_is_quiet(db_session):
    """台帳が上位の区画で書いてあるだけなら、食い違いではない。"""
    device = _device(db_session, "sw-02")
    _vlan(db_session, device, 10, "USERS")
    _svi(db_session, device, 10, "10.10.10.1/24")
    db_session.add(Vlan(vlan_id=10, name="USERS", subnet="10.10.0.0/16"))
    db_session.flush()

    assert not [i for i in vlanreview.review(db_session).findings if "区画" in i.finding.title]


def test_it_says_how_many_switches_it_could_not_look_at(db_session):
    """次に番号を決める人は、この一覧を見て空いている番号を選ぶ。

    読めていない機器があるのに黙ると、番号がそろっていると読まれる。
    """
    device = _device(db_session, "vlan-sw-01")
    device.device_type = DeviceType.SWITCH
    _vlan(db_session, device, 10, "USERS")
    db_session.add(
        Device(
            hostname="dark-sw-01",
            vendor="Cisco",
            model="C9300",
            device_type=DeviceType.SWITCH,
        )
    )
    db_session.flush()

    report = vlanreview.review(db_session)
    assert report.devices_with_vlans == 1
    assert any("2 台のうち 1 台" in note for note in report.notes)


def test_nothing_is_said_when_every_switch_was_looked_at(db_session):
    device = _device(db_session, "vlan-sw-02")
    device.device_type = DeviceType.SWITCH
    _vlan(db_session, device, 10, "USERS")
    db_session.flush()

    report = vlanreview.review(db_session)
    assert not any("この一覧に入っていません" in note for note in report.notes)


def test_a_japanese_ledger_name_is_not_called_a_disagreement(db_session):
    """台帳が日本語・機器が英数字、は食い違いではなく比べられないだけ。

    機器に日本語は入らない。これを「違います」と言うと、ほぼ全部の VLAN で
    同じ指摘が出て、本当に見てほしい食い違いが埋まる。
    """
    device = Device(hostname="sw-01", vendor="Cisco", device_type=DeviceType.SWITCH)
    db_session.add(device)
    db_session.add(Vlan(vlan_id=10, name="全社ユーザー"))
    db_session.flush()
    db_session.add(DeviceVlan(device_id=device.id, vlan_id=10, name="USERS"))
    db_session.commit()

    titles = [item.finding.title for item in vlanreview.review(db_session).findings]
    assert not [title for title in titles if "名前が違います" in title]


def test_two_ascii_names_that_differ_are_still_called_out(db_session):
    # 比べられるものは、これまでどおり指摘する
    device = Device(hostname="sw-01", vendor="Cisco", device_type=DeviceType.SWITCH)
    db_session.add(device)
    db_session.add(Vlan(vlan_id=10, name="USER-SEG"))
    db_session.flush()
    db_session.add(DeviceVlan(device_id=device.id, vlan_id=10, name="SERVERS"))
    db_session.commit()

    titles = [item.finding.title for item in vlanreview.review(db_session).findings]
    assert [title for title in titles if "名前が違います" in title]
