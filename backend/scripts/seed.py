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
        snapshots = _seed_configs(db, by_host)
        reference_counts = _seed_reference(db)
        contracts = _seed_branch_contracts(db)
        print(
            f"投入完了: 拠点{1 + wan['sites']}件 / 機器{len(devices) + wan['devices']}台 / "
            f"保守契約{5 + contracts}件 / 結線{len(wiring) + wan['links']}本 / "
            f"VLAN{len(segments)}件 / ポート{len(ports) + wan['interfaces']}件 / "
            f"Config{snapshots}件 / "
            f"型番{reference_counts[0]}件 / CVE{reference_counts[1]}件 / "
            f"該当{reference_counts[2]}件"
        )
    finally:
        db.close()


def _seed_branch_contracts(db) -> int:
    """拠点の機器にも保守契約を入れる。

    契約が1つも無いと、総合の重さが「不明」に倒れる。それ自体は正しい
    （保守の状態が分からないため）が、実際の台帳は全部が空でも全部が
    埋まってもいない。切れているもの・近いもの・余裕のあるものが混ざった
    形にして、最初の画面で色の違いが見えるようにする。
    """
    from app.models import Device, DeviceStatus, MaintenanceContract

    hosts = db.scalars(
        select(Device).where(Device.status != DeviceStatus.RETIRED).order_by(Device.hostname)
    ).all()
    already = {row for row in db.scalars(select(MaintenanceContract.device_id)).all()}

    # 切れている / 迫っている / 余裕がある / 契約が無い、を混ぜる
    spread = [-120, -20, 45, 120, 400, 900, None, None]
    added = 0
    for index, device in enumerate(hosts):
        if device.id in already:
            continue
        days = spread[index % len(spread)]
        if days is None:
            continue
        db.add(
            MaintenanceContract(
                device_id=device.id,
                provider="拠点一括保守",
                contract_number=f"MC-BR-{device.id:05d}",
                service_level="翌営業日" if days > 0 else "翌営業日（更新待ち）",
                start_date=date(2023, 4, 1),
                end_date=_d(days),
                annual_cost=60000,
            )
        )
        added += 1
    db.commit()
    return added


def _seed_reference(db) -> tuple[int, int, int]:
    """型番ごとの期限と、公表されている弱点を入れて、該当状況まで出す。

    ここが空だと、保守期限も対処状況も「まだ何も分からない」画面になる。
    どちらも台帳と突き合わせて初めて意味が出るところなので、見せる段では
    両方そろっている必要がある。

    取り込みは画面と同じ道（reference.import_products / import_vulnerabilities）を通す。
    """
    from pathlib import Path

    from app.services import exposure_schedule, reference
    from app.services.tablefile import read_table

    folder = Path(__file__).resolve().parent / "demo_reference"
    products = vulnerabilities = 0

    csv_path = folder / "products.csv"
    if csv_path.is_file():
        table = read_table(csv_path.name, csv_path.read_bytes())
        result = reference.import_products(db, table)
        products = result.created + result.updated

    json_path = folder / "vulnerabilities.json"
    if json_path.is_file():
        result = reference.import_vulnerabilities(db, json_path.read_bytes())
        vulnerabilities = result.created + result.updated

    _seed_templates(db, folder)
    _seed_scenario(db)
    _seed_remediations(db, folder)

    matched = (
        exposure_schedule.run(db, trigger="取り込み").created
        if (products or vulnerabilities)
        else 0
    )
    return products, vulnerabilities, matched


def _seed_scenario(db) -> int:
    """更改の検討案を1本、期限の近い機器から起こしておく。

    ここが空だと、シナリオの画面が「まず作ってください」だけになる。台帳に
    触らずに試算できる、というこの機能の値打ちが見えない。

    たたき台は画面と同じ道（scenarios.generate_items）で作る。手で並べた
    ものを置くと、実際の動きと違うものを見せることになる。
    """
    from datetime import date

    from app.models.scenario import Scenario, ScenarioStatus
    from app.services import scenarios

    name = "2027年度 更改の検討"
    if db.scalars(select(Scenario).where(Scenario.name == name)).first():
        return 0

    scenario = Scenario(
        name=name,
        description=(
            "保守期限とサポート終了（EoL）が近い機器を並べたたたき台です。"
            "台帳には触っていません。採用が決まったら台帳へ反映します。"
        ),
        status=ScenarioStatus.DRAFT,
        target_date=date(TODAY.year + 1, 3, 31),
    )
    db.add(scenario)
    db.flush()
    scenarios.generate_items(db, scenario, within_days=730)
    db.commit()
    return len(scenario.items)


def _seed_templates(db, folder) -> int:
    """設定変更用のテンプレートを入れる。

    ここが空だと、テンプレートの画面が何も無いまま開く。弱点への対処でも
    「テンプレートが紐付いていないので、手順の本文をそのままコマンドとして
    扱っています」という断り書きが毎回付く。
    """
    import json

    from app.models.document import ConfigTemplate, TemplateCategory

    path = folder / "config_templates.json"
    if not path.is_file():
        return 0

    added = 0
    for entry in json.loads(path.read_text(encoding="utf-8")):
        name = entry["name"]
        if db.scalars(select(ConfigTemplate).where(ConfigTemplate.name == name)).first():
            continue
        entry["category"] = TemplateCategory(entry.get("category", "other"))
        db.add(ConfigTemplate(**entry))
        added += 1
    db.commit()
    return added


def _seed_remediations(db, folder) -> int:
    """該当した弱点に、何をすればいいかまで入れる。

    公開データには「このコマンドで回避できる」が入っていない。該当だけ
    出して対処が空だと、見た人は結局アドバイザリを読みに行くことになり、
    この道具を通す意味が無くなる。

    人が一度調べた対処を残す、というモデルの使い方そのものを見せる。
    """
    import json

    from app.models import Remediation, Vulnerability

    path = folder / "remediations.json"
    if not path.is_file():
        return 0

    added = 0
    for entry in json.loads(path.read_text(encoding="utf-8")):
        cve_id = entry.pop("cve_id")
        vulnerability = db.scalars(
            select(Vulnerability).where(Vulnerability.cve_id == cve_id)
        ).first()
        if vulnerability is None:
            continue
        already = db.scalars(
            select(Remediation).where(
                Remediation.vulnerability_id == vulnerability.id,
                Remediation.summary == entry["summary"],
            )
        ).first()
        if already is not None:
            continue
        db.add(Remediation(vulnerability_id=vulnerability.id, **entry))
        added += 1
    db.commit()
    return added


def _seed_configs(db, by_host: dict) -> int:
    """本社の機器に、実物と同じ形の Config を入れる。

    台帳だけだと、点検・L3・計画の画面がどれも空のままになる。この道具の
    値打ちはそこから先にあるので、見せる段では中身が入っている必要がある。

    取り込みは画面と同じ道（configs.ingest）を通す。デモ用に別の道を作ると、
    そこだけ動いて本番で動かない、という形になる。
    """
    from pathlib import Path

    from app.services import configs

    folder = Path(__file__).resolve().parent / "demo_configs"
    if not folder.is_dir():
        return 0

    count = 0
    for path in sorted(folder.glob("*.txt")):
        device = by_host.get(path.stem)
        if device is None:
            continue
        try:
            configs.ingest(
                db,
                path.name,
                path.read_text(encoding="utf-8"),
                device_id=device.id,
                apply_inventory=False,
            )
        except Exception as error:  # noqa: BLE001  デモの投入で全体を止めない
            print(f"  Config を入れられませんでした（{path.name}）: {error}")
            continue
        count += 1
    return count


if __name__ == "__main__":
    # 空の DB に対しても、そのまま動くようにしておく。表が無いまま INSERT すると
    # SQLAlchemy の長い追跡が出るだけで、何をすればいいのか読み取れない
    from app.bootstrap import apply_schema

    apply_schema()
    seed(force="--force" in sys.argv)
