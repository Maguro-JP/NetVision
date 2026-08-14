"""全国規模の広域網をデモデータとして作る。

数台のサンプルでは分からないことがある。拠点が増えたときに

* 全体図は誰も見ない（見ても読めない）
* 拠点を選んで、その周り（対向まで）を見る
* 拠点名で探せないと、そもそも辿り着けない

という使い方になる。それを実際に触って確かめられるように、
大元1 / 東西センター2 / 地域支社7 / 都道府県拠点47 の構成を入れる。

構成はよくある形にしてある。

    都道府県拠点 → 地域支社 → 東西センター → 大元（本社データセンター）

拠点を上位でまたいで結ぶような、実際には引かない線は作らない。
デモとはいえ、あり得ない構成を見せると道具の側が信用されない。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Device, DeviceTunnel, DeviceType, Site
from app.models.topology import Interface, Link, LinkOrigin
from app.services.configparse import normalize

# 地域支社と、その配下の都道府県。センターの東西もここで決める
REGIONS: list[tuple[str, str, str, str, list[str]]] = [
    # (支社コード, 支社名, 所在地, 東西, 都道府県)
    ("hkd", "北海道支社", "北海道札幌市", "east", ["北海道"]),
    (
        "thk",
        "東北支社",
        "宮城県仙台市",
        "east",
        ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    ),
    (
        "knt",
        "関東支社",
        "埼玉県さいたま市",
        "east",
        [
            "茨城県",
            "栃木県",
            "群馬県",
            "埼玉県",
            "千葉県",
            "東京都",
            "神奈川県",
            "山梨県",
            "新潟県",
            "長野県",
        ],
    ),
    (
        "cbu",
        "中部支社",
        "愛知県名古屋市",
        "west",
        ["富山県", "石川県", "福井県", "岐阜県", "静岡県", "愛知県", "三重県"],
    ),
    (
        "ksi",
        "関西支社",
        "大阪府大阪市",
        "west",
        ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    ),
    (
        "cgk",
        "中国四国支社",
        "広島県広島市",
        "west",
        [
            "鳥取県",
            "島根県",
            "岡山県",
            "広島県",
            "山口県",
            "徳島県",
            "香川県",
            "愛媛県",
            "高知県",
        ],
    ),
    (
        "kyu",
        "九州支社",
        "福岡県福岡市",
        "west",
        ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"],
    ),
]

# ホスト名に使う都道府県のローマ字。実際の命名規則もだいたいこの形になる
ROMAJI = {
    "北海道": "hokkaido",
    "青森県": "aomori",
    "岩手県": "iwate",
    "宮城県": "miyagi",
    "秋田県": "akita",
    "山形県": "yamagata",
    "福島県": "fukushima",
    "茨城県": "ibaraki",
    "栃木県": "tochigi",
    "群馬県": "gunma",
    "埼玉県": "saitama",
    "千葉県": "chiba",
    "東京都": "tokyo",
    "神奈川県": "kanagawa",
    "新潟県": "niigata",
    "富山県": "toyama",
    "石川県": "ishikawa",
    "福井県": "fukui",
    "山梨県": "yamanashi",
    "長野県": "nagano",
    "岐阜県": "gifu",
    "静岡県": "shizuoka",
    "愛知県": "aichi",
    "三重県": "mie",
    "滋賀県": "shiga",
    "京都府": "kyoto",
    "大阪府": "osaka",
    "兵庫県": "hyogo",
    "奈良県": "nara",
    "和歌山県": "wakayama",
    "鳥取県": "tottori",
    "島根県": "shimane",
    "岡山県": "okayama",
    "広島県": "hiroshima",
    "山口県": "yamaguchi",
    "徳島県": "tokushima",
    "香川県": "kagawa",
    "愛媛県": "ehime",
    "高知県": "kochi",
    "福岡県": "fukuoka",
    "佐賀県": "saga",
    "長崎県": "nagasaki",
    "熊本県": "kumamoto",
    "大分県": "oita",
    "宮崎県": "miyazaki",
    "鹿児島県": "kagoshima",
    "沖縄県": "okinawa",
}


def seed_wan(db: Session, core_router: Device) -> dict[str, int]:
    """広域網を作る。`core_router` は大元（本社データセンター）の出口ルータ。"""
    sites: dict[str, Site] = {}
    devices: list[Device] = []
    links: list[tuple[str, str, str, str]] = []
    interfaces: list[tuple[str, str, str | None, int | None, int | None, str | None]] = []
    # (機器, トンネル名, 種別, 宛先IP, 説明)。物理では支社を経由していても、
    # 論理ではセンターと直結して見える——そのズレを図で見せるために入れる
    tunnels: list[tuple[str, str, str, str, str]] = []

    def site(name: str, address: str) -> Site:
        entry = Site(name=name, address=address)
        db.add(entry)
        sites[name] = entry
        return entry

    # --- 東西センター ---
    centers = {
        "east": site("東日本センター", "東京都江東区"),
        "west": site("西日本センター", "大阪府大阪市北区"),
    }
    db.flush()

    for index, (side, name) in enumerate((("east", "east"), ("west", "west")), start=1):
        center = centers[side]
        # センターは冗長構成。片系停止の影響を図で追えるようにする
        for unit in (1, 2):
            devices.append(
                Device(
                    hostname=f"{name}-core-rt-{unit:02d}",
                    vendor="Juniper",
                    model="MX304",
                    device_type=DeviceType.ROUTER,
                    os_name="Junos",
                    os_version="22.4R3",
                    site_id=center.id,
                    mgmt_ip=f"10.{index}.0.{unit}",
                )
            )
        devices.append(
            Device(
                hostname=f"{name}-core-sw-01",
                vendor="Cisco",
                model="C9500-48Y4C",
                device_type=DeviceType.SWITCH,
                os_name="IOS-XE",
                os_version="17.12.03",
                site_id=center.id,
                mgmt_ip=f"10.{index}.0.11",
            )
        )

    # --- 地域支社と都道府県拠点 ---
    for region_index, (code, region_name, address, side, prefectures) in enumerate(
        REGIONS, start=1
    ):
        branch_site = site(region_name, address)
        db.flush()
        branch_rt = f"{code}-br-rt-01"
        devices.append(
            Device(
                hostname=branch_rt,
                vendor="Cisco",
                model="ISR4451-X",
                device_type=DeviceType.ROUTER,
                os_name="IOS-XE",
                os_version="17.09.04a",
                site_id=branch_site.id,
                mgmt_ip=f"10.{10 + region_index}.0.1",
            )
        )
        devices.append(
            Device(
                hostname=f"{code}-br-sw-01",
                vendor="Cisco",
                model="C9300-48P",
                device_type=DeviceType.SWITCH,
                os_name="IOS-XE",
                os_version="17.09.04a",
                site_id=branch_site.id,
                mgmt_ip=f"10.{10 + region_index}.0.11",
            )
        )
        links.append(
            (branch_rt, "GigabitEthernet0/0/1", f"{code}-br-sw-01", "GigabitEthernet1/0/1")
        )
        # 支社は東西どちらかのセンターへ上がる。センター2台のうち1号機へ
        links.append(
            (
                branch_rt,
                "GigabitEthernet0/0/0",
                f"{side}-core-rt-01",
                f"ge-0/0/{region_index}",
            )
        )

        for pref_index, prefecture in enumerate(prefectures, start=1):
            name = ROMAJI[prefecture]
            branch_office = site(f"{prefecture}営業所", prefecture)
            db.flush()
            station_rt = f"{name}-rt-01"
            station_sw = f"{name}-sw-01"
            devices.append(
                Device(
                    hostname=station_rt,
                    vendor="NEC",
                    model="IX2215",
                    device_type=DeviceType.ROUTER,
                    os_name="IX",
                    os_version="10.6.21",
                    site_id=branch_office.id,
                    mgmt_ip=f"10.{100 + region_index}.{pref_index}.1",
                )
            )
            devices.append(
                Device(
                    hostname=station_sw,
                    vendor="NEC",
                    model="QX-S5528P",
                    device_type=DeviceType.SWITCH,
                    os_name="Comware",
                    os_version="7.1.070",
                    site_id=branch_office.id,
                    mgmt_ip=f"10.{100 + region_index}.{pref_index}.11",
                )
            )
            links.append((station_rt, "GigaEthernet0/1", station_sw, "GigabitEthernet1/0/25"))
            links.append(
                (station_rt, "GigaEthernet0/0", branch_rt, f"GigabitEthernet0/1/{pref_index}")
            )
            # 拠点スイッチにも VLAN を持たせる。L2 図が拠点で空だとデモにならない
            for port in range(1, 5):
                interfaces.append(
                    (station_sw, f"GigabitEthernet1/0/{port}", "access", 10, None, None)
                )
            for port in range(5, 25):
                interfaces.append(
                    (station_sw, f"GigabitEthernet1/0/{port}", None, None, None, None)
                )
            interfaces.append((station_sw, "GigabitEthernet1/0/25", "trunk", None, 1, "10,30"))
            # 拠点からセンターへは IPsec で直に張る。物理は支社経由、論理は直結
            tunnels.append(
                (
                    station_rt,
                    "Tunnel0",
                    "ipsec",
                    f"10.{1 if side == 'east' else 2}.0.1",
                    f"{'東' if side == 'east' else '西'}日本センター向け",
                )
            )

    # センターのコアスイッチにもトランクを付ける（L2 がセンターで空にならないように）
    for side in ("east", "west"):
        interfaces.append(
            (f"{side}-core-sw-01", "TenGigabitEthernet1/0/1", "trunk", None, 1, "10,30,40")
        )

    db.add_all(devices)
    db.flush()

    by_host = {d.hostname: d for d in devices}
    by_host[core_router.hostname] = core_router

    # センターは大元へ上がる。ここが全体の頂点
    for index, side in enumerate(("east", "west"), start=1):
        links.append((f"{side}-core-rt-01", "ge-0/0/0", core_router.hostname, f"ge-0/0/{index}"))
        links.append((f"{side}-core-rt-01", "ge-0/1/0", f"{side}-core-rt-02", "ge-0/1/0"))
        links.append(
            (f"{side}-core-rt-01", "ge-0/2/0", f"{side}-core-sw-01", "TenGigabitEthernet1/0/1")
        )

    db.add_all(
        [
            Link(
                a_device_id=by_host[a].id,
                a_interface=a_if,
                b_device_id=by_host[b].id,
                b_interface=b_if,
                origin=LinkOrigin.DISCOVERED,
                protocol="lldp",
                confirmed_both_ways=True,
            )
            for a, a_if, b, b_if in links
            if a in by_host and b in by_host
        ]
    )
    db.add_all(
        [
            DeviceTunnel(
                device_id=by_host[host].id,
                name=name,
                kind=kind,
                source=None,
                destination=destination,
                description=description,
            )
            for host, name, kind, destination, description in tunnels
            if host in by_host
        ]
    )
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
            )
            for host, name, mode, access, native, trunk in interfaces
            if host in by_host
        ]
    )
    db.commit()

    return {
        "sites": len(sites),
        "devices": len(devices),
        "links": len(links),
        "interfaces": len(interfaces),
        "tunnels": len(tunnels),
    }
