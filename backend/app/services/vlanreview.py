"""VLAN の名前が、機器と台帳でそろっているかを見る。

VLAN は番号でつながる。名前は人のためのものだが、そこが食い違っていると
現場が止まる。「10 番は SRV」と思っている人と「10 番は 業務」と思っている人が
同じ線を触ると、切ってはいけないものを切る。

1台ずつの Config を見ても分からない。番号ごとに、機器をまたいで見て初めて
「同じ番号に3通りの名前が付いている」と言える。ここはそれだけを行う。

名前の比べ方は、揺れを吸ってから比べる。SRV と srv、USER-SEG と USER_SEG は
同じものとして扱う。ここを厳密にすると、直す必要の無いものまで並ぶ。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, DeviceType
from app.models.topology import Interface
from app.models.vlan import DeviceVlan, Vlan
from app.services.reviewnote import HIGH, LOW, MEDIUM, Finding

#: 名前を並べて出す上限。全部出すと、どれが多数派なのか読めなくなる
_SHOW = 4


@dataclass
class VlanFinding:
    """VLAN 1つについての指摘。どの番号かが分からないと直しに行けない。"""

    vlan_id: int
    finding: Finding


@dataclass
class VlanReport:
    vlans_seen: int = 0
    devices_with_vlans: int = 0
    findings: list[VlanFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def review(db: Session) -> VlanReport:
    report = VlanReport()
    rows = list(db.scalars(select(DeviceVlan)))
    if not rows:
        report.notes.append(
            "機器から読み取った VLAN がありません。Config を取り込むと、"
            "番号ごとに名前がそろっているかを見られます。"
        )
        return report

    hostnames = {device.id: device.hostname for device in db.scalars(select(Device))}
    ledger = {row.vlan_id: row for row in db.scalars(select(Vlan))}

    by_vlan: dict[int, list[DeviceVlan]] = {}
    for row in rows:
        by_vlan.setdefault(row.vlan_id, []).append(row)

    # SVI に振ってあるアドレス。台帳のセグメントと突き合わせるために使う
    svi_addresses: dict[int, list[tuple[int, str]]] = {}
    for interface in db.scalars(select(Interface).where(Interface.ip_address.isnot(None))):
        match = re.match(r"^vlan(\d+)$", interface.normalized_name or "", re.IGNORECASE)
        if match and interface.ip_address:
            svi_addresses.setdefault(int(match.group(1)), []).append(
                (interface.device_id, interface.ip_address)
            )

    report.vlans_seen = len(by_vlan)
    report.devices_with_vlans = len({row.device_id for row in rows})

    for vlan_id in sorted(by_vlan):
        entries = by_vlan[vlan_id]
        report.findings.extend(_names_disagree(vlan_id, entries, hostnames))
        report.findings.extend(_ledger_disagrees(vlan_id, entries, ledger, hostnames))
        report.findings.extend(_not_in_ledger(vlan_id, entries, ledger))
        report.findings.extend(_ledger_subnet_disagrees(vlan_id, ledger, svi_addresses, hostnames))

    # 読めていない機器があるのに黙ると、番号がそろっていると読まれる。
    # 次に番号を決める人は、この一覧を見て空いている番号を選ぶ
    switches = db.scalars(select(Device).where(Device.device_type == DeviceType.SWITCH)).all()
    if switches and report.devices_with_vlans < len(switches):
        report.notes.append(
            f"見たのは、台帳のスイッチ {len(switches)} 台のうち "
            f"{report.devices_with_vlans} 台です。"
            "残りの機器で使われている VLAN は、この一覧に入っていません。"
        )

    order = {HIGH: 0, MEDIUM: 1, LOW: 2}
    report.findings.sort(key=lambda item: (order.get(item.finding.level, 3), item.vlan_id))
    return report


def _key(name: str | None) -> str:
    """比べるための形。大文字小文字と区切り記号の揺れは同じものとして扱う。"""
    if not name:
        return ""
    return re.sub(r"[\s_\-.]", "", name).lower()


def _label(entries: list[DeviceVlan], hostnames: dict[int, str]) -> list[str]:
    return [
        f"{hostnames.get(entry.device_id, entry.device_id)}: {entry.name or '名前なし'}"
        for entry in entries[:_SHOW]
    ]


def _ledger_subnet_disagrees(
    vlan_id: int,
    ledger: dict[int, Vlan],
    svi_addresses: dict[int, list[tuple[int, str]]],
    hostnames: dict[int, str],
) -> list[VlanFinding]:
    """台帳に書いてあるセグメントと、実機が名乗っているアドレスが違う。

    名前の食い違いより重い。次に番号を決める人は台帳の区画から住所を選ぶので、
    そこが実機と違うと、選んだ住所がそのセグメントに載らない。他所と重なる
    こともある。設定は通るので、繋いでみるまで気づけない。
    """
    entry = ledger.get(vlan_id)
    if entry is None or not entry.subnet:
        return []
    try:
        network = ipaddress.ip_network(entry.subnet, strict=False)
    except ValueError:
        return []

    outside: list[str] = []
    for device_id, text in svi_addresses.get(vlan_id, []):
        try:
            address = ipaddress.ip_interface(text).ip
        except ValueError:
            continue
        if address.version != network.version or address not in network:
            outside.append(f"{hostnames.get(device_id, device_id)}: {text}")
    if not outside:
        return []

    return [
        VlanFinding(
            vlan_id=vlan_id,
            finding=Finding(
                level=MEDIUM,
                title=f"VLAN {vlan_id} は、台帳の区画に実機のアドレスが入っていません",
                why="次に番号を決める人は、台帳の区画から住所を選びます。そこが実機と"
                "違うと、選んだ住所はそのセグメントに載らず、他所と重なることもあり"
                "ます。設定は通るので、繋いでみるまで気づけません。どちらが正しいか"
                "は、ここでは決められません。",
                evidence=[f"台帳: {entry.subnet}", *outside[:_SHOW]],
            ),
        )
    ]


def _names_disagree(
    vlan_id: int, entries: list[DeviceVlan], hostnames: dict[int, str]
) -> list[VlanFinding]:
    """同じ番号に、機器ごとに違う名前が付いている。"""
    named = [entry for entry in entries if entry.name]
    keys = {_key(entry.name) for entry in named}
    if len(keys) < 2:
        return []
    return [
        VlanFinding(
            vlan_id=vlan_id,
            finding=Finding(
                level=MEDIUM,
                title=f"VLAN {vlan_id} の名前が、機器によって違います",
                why="番号は同じなので通信はつながりますが、人の側で食い違います。"
                "「10 番を止めて」と言われたときに、別の機器で別の意味に取られます。"
                "作業の指示が通らなくなるので、名前はそろえてください。",
                evidence=_label(named, hostnames),
            ),
        )
    ]


def _comparable(ledger_name: str, device_name: str) -> bool:
    """その2つの名前を、突き合わせられるか。

    台帳は日本語、機器は英数字、という組み合わせが現場では普通にある。
    機器に日本語は入らないので、これは食い違いではなく、そもそも比べられない。
    比べられないものを「違います」と言うと、全部の VLAN で同じ指摘が出て、
    本当に見てほしい食い違いが埋まる。
    """
    return ledger_name.isascii() or not device_name.isascii()


def _ledger_disagrees(
    vlan_id: int,
    entries: list[DeviceVlan],
    ledger: dict[int, Vlan],
    hostnames: dict[int, str],
) -> list[VlanFinding]:
    """台帳に書いた名前と、機器に入っている名前が違う。"""
    row = ledger.get(vlan_id)
    if row is None or not row.name:
        return []
    differing = [
        entry
        for entry in entries
        if entry.name and _key(entry.name) != _key(row.name) and _comparable(row.name, entry.name)
    ]
    if not differing:
        return []
    return [
        VlanFinding(
            vlan_id=vlan_id,
            finding=Finding(
                level=LOW,
                title=f"VLAN {vlan_id} は、台帳と機器で名前が違います",
                why="どちらが正しいのかは、ここでは決められません。"
                "台帳が新しいなら機器へ、機器が実態なら台帳へ、どちらかに寄せてください。"
                "放っておくと、資料を見た人と機器を見た人で話が合わなくなります。",
                evidence=[f"台帳: {row.name}", *_label(differing, hostnames)],
            ),
        )
    ]


def _not_in_ledger(
    vlan_id: int, entries: list[DeviceVlan], ledger: dict[int, Vlan]
) -> list[VlanFinding]:
    """機器には入っているが、台帳に無い番号。

    1台にしか無いものは、試したまま残っている可能性がある。何台にも入って
    いるなら、実際に使われているのに台帳から漏れている。後者の方が重い。
    """
    if vlan_id in ledger:
        return []
    if len(entries) < 2:
        return []
    return [
        VlanFinding(
            vlan_id=vlan_id,
            finding=Finding(
                level=LOW,
                title=f"VLAN {vlan_id} が台帳にありません（{len(entries)}台に入っています）",
                why="何台にも入っているので、使われている番号です。"
                "台帳に無いと、次に番号を決める人が同じ番号を別の用途に使います。",
                evidence=[entry.name or f"VLAN {vlan_id}" for entry in entries[:_SHOW]],
            ),
        )
    ]
