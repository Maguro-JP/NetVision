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
    MaintenanceContract,
    Site,
)
from app.models.change_log import ChangeSource
from app.models.topology import Link, LinkOrigin
from app.services.audit import audit_context

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

        hq = Site(name="本社データセンター", address="東京都千代田区")
        branch = Site(name="大阪支社", address="大阪府大阪市")
        db.add_all([hq, branch])
        db.flush()

        devices = [
            Device(
                hostname="core-sw-01",
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
                vendor="Fortinet",
                model="FortiGate-100F",
                serial_number="FG100F0001",
                device_type=DeviceType.FIREWALL,
                os_name="FortiOS",
                os_version="7.2.8",
                site_id=branch.id,
                mgmt_ip="10.1.0.1",
                install_date=date(2022, 6, 15),
                eol_date=_d(900),
            ),
            Device(
                hostname="acc-sw-osaka-01",
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
                    device_id=by_host["acc-sw-osaka-01"].id,
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
            (
                "core-sw-01",
                "TenGigabitEthernet1/1/1",
                "core-sw-02",
                "TenGigabitEthernet1/1/1",
                True,
            ),
            ("core-sw-01", "GigabitEthernet1/0/1", "acc-sw-osaka-01", "GigabitEthernet0/1", True),
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
        # VLAN も入れておく。デモで絞り込みを試せないと、機能があること自体が伝わらない。
        # 実運用では Config の `vlan 10 / name USERS` から取り込まれる部分。
        segments = [
            (10, "USERS", ["core-sw-01", "core-sw-02", "acc-sw-osaka-01"]),
            (20, "SERVERS", ["core-sw-01", "core-sw-02"]),
            (30, "MGMT", ["core-sw-01", "core-sw-02", "acc-sw-osaka-01", "wlc-01"]),
            (40, "WIFI", ["core-sw-02", "wlc-01"]),
        ]
        db.add_all(
            [
                DeviceVlan(device_id=by_host[host].id, vlan_id=vlan_id, name=name)
                for vlan_id, name, hosts in segments
                for host in hosts
            ]
        )
        db.commit()
        print(
            f"投入完了: 拠点2件 / 機器{len(devices)}台 / 保守契約5件 / "
            f"結線{len(wiring)}本 / VLAN{len(segments)}件"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
