"""デモ用のサンプルデータを投入する。

    python -m scripts.seed

既存データがある場合は何もしない（--force で全削除して再投入）。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Device,
    DeviceStatus,
    DeviceType,
    DeviceVlan,
    DeviceVrf,
    MaintenanceContract,
    Site,
    SiteStatus,
    Vlan,
)
from app.models.change_log import ChangeSource
from app.models.topology import Interface, Link, LinkOrigin
from app.services import topology
from app.services.audit import audit_context
from app.services.configparse import normalize
from scripts.seed_wan import seed_wan

TODAY = date.today()


def _d(days: int) -> date:
    return TODAY + timedelta(days=days)


def seed(force: bool = False) -> None:
    # 投入したデータも変更履歴に残す（発生元は seed として区別できる）
    with audit_context(ChangeSource.SEED, note="デモデータの投入"):
        _seed(force)


def _seed(force: bool) -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(Device).limit(1)):
            if not force:
                print("既にデータがあります。--force で再投入できます。")
                return
            db.query(MaintenanceContract).delete()
            db.query(Device).delete()
            db.query(Site).delete()
            db.commit()

        hq = Site(
            name="本社データセンター",
            code="TOK-DC1",
            address="東京都千代田区",
            opened_on=date(2018, 4, 1),
        )
        db.add(hq)
        db.flush()
        # 出口・FW・コアは同じ拠点に置く。回線が拠点をまたいで往復するような
        # 構成は実際には引かない。デモでも、あり得ない絵を見せない
        branch = hq

        devices = [
            Device(
                hostname="core-sw-01",
                code="NW-00101",
                label="本社コア1号機",
                vendor="Cisco",
                model="C9500-40X",
                serial_number="FOC2401A001",
                device_type=DeviceType.SWITCH,
                os_name="IOS-XE",
                os_version="17.09.04a",
                site_id=hq.id,
                rack="A-01",
                rack_unit="U40-41",
                mgmt_ip="10.0.0.11",
                install_date=date(2021, 4, 1),
                eos_date=_d(200),
                eol_date=_d(1300),
            ),
            Device(
                hostname="core-sw-02",
                code="NW-00102",
                label="本社コア2号機",
                vendor="Cisco",
                model="C9500-40X",
                serial_number="FOC2401A002",
                device_type=DeviceType.SWITCH,
                os_name="IOS-XE",
                os_version="17.09.04a",
                site_id=hq.id,
                rack="A-02",
                mgmt_ip="10.0.0.12",
                install_date=date(2021, 4, 1),
                eos_date=_d(200),
                eol_date=_d(1300),
            ),
            Device(
                hostname="edge-rt-01",
                code="NW-00001",
                label="本社出口ルーター",
                vendor="Juniper",
                model="MX204",
                serial_number="JN1234ABC",
                device_type=DeviceType.ROUTER,
                os_name="Junos",
                os_version="21.4R3-S5",
                site_id=hq.id,
                mgmt_ip="10.0.0.1",
                install_date=date(2019, 10, 1),
                eos_date=_d(-300),
                eol_date=_d(45),  # サポート終了間近
            ),
            Device(
                hostname="fw-01",
                code="NW-00002",
                label="本社ファイアウォール",
                vendor="Fortinet",
                model="FortiGate-100F",
                serial_number="FG100F0001",
                device_type=DeviceType.FIREWALL,
                os_name="FortiOS",
                os_version="7.2.8",
                site_id=branch.id,
                mgmt_ip="10.0.0.2",
                install_date=date(2022, 6, 15),
                eol_date=_d(900),
            ),
            Device(
                hostname="acc-sw-01",
                code="NW-00201",
                vendor="Cisco",
                model="C2960X-48TS-L",
                device_type=DeviceType.SWITCH,
                os_name="IOS",
                os_version="15.2(7)E6",
                site_id=branch.id,
                mgmt_ip="10.1.0.21",
                install_date=date(2016, 3, 1),
                eos_date=_d(-1500),
                eol_date=_d(-200),  # サポート切れ
            ),
            Device(
                hostname="wlc-01",
                code="NW-00301",
                vendor="Cisco",
                model="C9800-L-C-K9",
                device_type=DeviceType.WIRELESS_CONTROLLER,
                os_name="IOS-XE",
                os_version="17.12.03",
                site_id=hq.id,
                mgmt_ip="10.0.0.31",
                install_date=date(2023, 9, 1),
                status=DeviceStatus.ACTIVE,
            ),
        ]
        db.add_all(devices)
        db.flush()

        by_host = {d.hostname: d for d in devices}
        db.add_all(
            [
                MaintenanceContract(
                    device_id=by_host["core-sw-01"].id,
                    provider="ネットワンシステムズ",
                    contract_number="MC-2024-0011",
                    service_level="24x365 4時間駆付",
                    start_date=date(2024, 4, 1),
                    end_date=_d(240),
                    annual_cost=480000,
                ),
                MaintenanceContract(
                    device_id=by_host["core-sw-02"].id,
                    provider="ネットワンシステムズ",
                    contract_number="MC-2024-0012",
                    service_level="24x365 4時間駆付",
                    start_date=date(2024, 4, 1),
                    end_date=_d(240),
                    annual_cost=480000,
                ),
                MaintenanceContract(
                    device_id=by_host["edge-rt-01"].id,
                    provider="Juniper Care",
                    service_level="NBD",
                    start_date=date(2023, 10, 1),
                    end_date=_d(60),  # 保守切れ間近
                    annual_cost=720000,
                ),
                MaintenanceContract(
                    device_id=by_host["fw-01"].id,
                    provider="SB C&S",
                    service_level="FortiCare 24x7",
                    start_date=date(2022, 6, 15),
                    end_date=_d(800),
                    annual_cost=260000,
                ),
                MaintenanceContract(
                    device_id=by_host["acc-sw-01"].id,
                    provider="ネットワンシステムズ",
                    service_level="平日日中",
                    start_date=date(2019, 4, 1),
                    end_date=_d(-400),  # 保守切れ
                    annual_cost=90000,
                ),
            ]
        )

        # 結線も入れておく。デモで構成図が空だと、何を見せたいのか伝わらない。
        # 実運用では Config の LLDP / CDP から自動で引かれる部分。
        wiring = [
            ("edge-rt-01", "ge-0/0/0", "fw-01", "port1", True),
            ("fw-01", "port2", "core-sw-01", "GigabitEthernet1/0/24", True),
            ("fw-01", "port3", "core-sw-02", "GigabitEthernet1/0/23", True),
            (
                "core-sw-01",
                "TenGigabitEthernet1/1/1",
                "core-sw-02",
                "TenGigabitEthernet1/1/1",
                True,
            ),
            ("core-sw-01", "GigabitEthernet1/0/1", "acc-sw-01", "GigabitEthernet0/1", True),
            # 片側からしか見えていない結線（画面では破線になる）
            ("core-sw-02", "GigabitEthernet1/0/10", "wlc-01", "GigabitEthernet1", False),
        ]
        db.add_all(
            [
                Link(
                    a_device_id=by_host[a].id,
                    a_interface=a_if,
                    b_device_id=by_host[b].id,
                    b_interface=b_if,
                    origin=LinkOrigin.DISCOVERED,
                    protocol="lldp",
                    confirmed_both_ways=confirmed,
                )
                for a, a_if, b, b_if, confirmed in wiring
            ]
        )
        # 同じ機器間の2本目。同じ辺に複数本来たときに引き出し口が散ることを
        # デモで見せる。人が引いた線は、段を飛ばすと自動配置が崩れるので足さない
        db.add_all(
            [
                Link(
                    a_device_id=by_host["core-sw-01"].id,
                    a_interface="TenGigabitEthernet1/1/2",
                    b_device_id=by_host["core-sw-02"].id,
                    b_interface="TenGigabitEthernet1/1/2",
                    origin=LinkOrigin.DISCOVERED,
                    protocol="lldp",
                    confirmed_both_ways=True,
                ),
            ]
        )
        # VLAN も入れておく。デモで絞り込みを試せないと、機能があること自体が伝わらない。
        # 実運用では Config の `vlan 10 / name USERS` から取り込まれる部分。
        segments = [
            (10, "USERS", ["core-sw-01", "core-sw-02", "acc-sw-01"]),
            (20, "SERVERS", ["core-sw-01", "core-sw-02"]),
            (30, "MGMT", ["core-sw-01", "core-sw-02", "acc-sw-01", "wlc-01"]),
            (40, "WIFI", ["core-sw-02", "wlc-01"]),
        ]
        db.add_all(
            [
                DeviceVlan(device_id=by_host[host].id, vlan_id=vlan_id, name=name)
                for vlan_id, name, hosts in segments
                for host in hosts
            ]
        )

        # VLAN 台帳。実機の名前（USERS）と、人が書く用途は別に持つ。
        # VLAN 20 だけ台帳に入れないでおくと「未登録」の見え方が試せる
        db.add_all(
            [
                Vlan(
                    vlan_id=10,
                    name="全社ユーザー",
                    purpose="社員端末（有線）",
                    owner="情報システム部",
                    subnet="10.10.0.0/16",
                ),
                Vlan(
                    vlan_id=30,
                    name="管理セグメント",
                    purpose="機器の管理IP・監視",
                    owner="情報システム部",
                    subnet="192.168.30.0/24",
                ),
                Vlan(vlan_id=40, name="無線", purpose="社内 Wi-Fi", owner="情報システム部"),
                # 設計しただけで、まだどの機器にも入っていない VLAN
                Vlan(vlan_id=50, name="来客用", purpose="ゲスト Wi-Fi（インターネットのみ）"),
                Vlan(
                    vlan_id=999,
                    name="未使用",
                    purpose="トランクのネイティブVLAN専用（通信させない）",
                    owner="情報システム部",
                ),
            ]
        )

        # ポートの VLAN 設定。アクセス / ネイティブ / トランクを分けて持つ。
        # acc-sw-01 側だけ VLAN 20 を許可していない状態にしてあり、
        # 「片側だけ許可漏れ」が図の上でどう出るかを試せるようにしてある
        ports = [
            ("core-sw-01", "TenGigabitEthernet1/1/1", "trunk", None, 999, "10,20,30,40", None),
            ("core-sw-02", "TenGigabitEthernet1/1/1", "trunk", None, 999, "10,20,30,40", None),
            ("core-sw-01", "GigabitEthernet1/0/1", "trunk", None, 999, "10,20,30", None),
            ("acc-sw-01", "GigabitEthernet0/1", "trunk", None, 999, "10,30", None),
            ("acc-sw-01", "GigabitEthernet0/10", "access", 10, None, None, None),
            ("core-sw-02", "GigabitEthernet1/0/10", "trunk", None, 1, "30,40", None),
            ("wlc-01", "GigabitEthernet1", "trunk", None, 999, "30,40", None),
            # L3 図用。ルーター〜FW の点対点に IP を付けておく
            ("edge-rt-01", "ge-0/0/0", None, None, None, None, "10.255.0.1/30"),
            ("fw-01", "port1", None, None, None, None, "10.255.0.2/30"),
            ("fw-01", "port2", None, None, None, None, "10.255.0.5/30"),
            ("fw-01", "port3", None, None, None, None, "10.255.0.9/30"),
            ("core-sw-01", "GigabitEthernet1/0/24", None, None, None, None, "10.255.0.6/30"),
            ("core-sw-02", "GigabitEthernet1/0/23", None, None, None, None, "10.255.0.10/30"),
        ]
        db.add_all(
            [
                Interface(
                    device_id=by_host[host].id,
                    name=name,
                    normalized_name=normalize(name),
                    mode=mode,
                    vlan=access,
                    native_vlan=native,
                    trunk_vlans=trunk,
                    ip_address=ip,
                )
                for host, name, mode, access, native, trunk, ip in ports
            ]
        )

        # VRF。ルーターの中を社内系と顧客系に分けている想定
        db.add_all(
            [
                DeviceVrf(device_id=by_host[host].id, name=name, rd=rd, description=description)
                for host, name, rd, description in [
                    ("core-sw-01", "SHANAI", "65000:100", "社内系"),
                    ("core-sw-02", "SHANAI", "65000:100", "社内系"),
                    ("core-sw-01", "KOKYAKU", "65000:200", "顧客提供系"),
                ]
            ]
        )
        db.commit()

        # 図の枠。機能があっても、デモに1つも無いと気づけない。
        # 現場の単位（ラック）と、意味の単位（出口まわり）を入れ、さらに
        # 部屋・フロアの枠で囲む。機材がどこに置いてあるかが図だけで分かる
        floor = topology.create_group(db, "physical", "本社ビル 3F", [])
        machine_room = topology.create_group(db, "physical", "301 マシン室", [], parent_id=floor.id)
        topology.create_group(
            db,
            "physical",
            "ラック A-01",
            [by_host["core-sw-01"].id],
            parent_id=machine_room.id,
        )
        topology.create_group(
            db,
            "physical",
            "ラック A-02",
            [by_host["core-sw-02"].id],
            parent_id=machine_room.id,
        )
        topology.create_group(
            db,
            "physical",
            "出口まわり",
            [by_host["edge-rt-01"].id, by_host["fw-01"].id],
            parent_id=floor.id,
        )
        db.commit()

        # 全国規模の広域網。数台のサンプルだけだと「拠点が増えたときにどう使うか」を
        # 試せない。拠点を選ぶ・対向まで見る・名前で探す、が要る規模を入れておく
        wan = seed_wan(db, by_host["edge-rt-01"])

        # 拠点は開いたり閉じたりする。機器を消さずに状態だけ変える運用を、
        # デモでも見えるようにしておく（移転・一時閉鎖・閉店）
        moved = db.scalar(select(Site).where(Site.name == "千葉県営業所"))
        if moved is not None:
            moved.address = "千葉県千葉市中央区（2026年2月に移転）"
        paused = db.scalar(select(Site).where(Site.name == "秋田県営業所"))
        if paused is not None:
            paused.status = SiteStatus.SUSPENDED
            paused.notes = "建て替えのため休業中。機器は据え置き"
        shut = db.scalar(select(Site).where(Site.name == "青森県営業所"))
        if shut is not None:
            shut.status = SiteStatus.CLOSED
            shut.closed_on = date(2026, 6, 30)
            shut.notes = "閉店。回線解約済み、機器の引き上げ待ち"
        db.commit()
        print(
            f"投入完了: 拠点{1 + wan['sites']}件 / 機器{len(devices) + wan['devices']}台 / "
            f"保守契約5件 / 結線{len(wiring) + wan['links']}本 / "
            f"VLAN{len(segments)}件 / ポート{len(ports) + wan['interfaces']}件"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
