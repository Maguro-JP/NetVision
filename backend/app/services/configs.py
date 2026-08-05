"""Config の取り込みと、そこからの構成情報の反映。

方針は「観測値と台帳値を分けて持ち、自動で上書きしない」こと。
Config から読み取った値は snapshot に残し、台帳（devices）との差分を提示して、
人が承認したときだけ反映する。実機と台帳が食い違っていること自体が情報だからである。
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.models.change_log import ChangeSource
from app.models.topology import ConfigSnapshot, Interface, Link, LinkOrigin
from app.services import configparse
from app.services.audit import audit_context
from app.services.configparse import ParsedConfig, Vendor, normalize, normalize_hostname

# Config から台帳へ反映できる項目
INVENTORY_FIELDS = ("vendor", "model", "serial_number", "os_name", "os_version")

FIELD_LABELS = {
    "vendor": "ベンダー",
    "model": "型番",
    "serial_number": "シリアル番号",
    "os_name": "OS名",
    "os_version": "OSバージョン",
}


@dataclass
class FieldDiff:
    field: str
    label: str
    current: str | None  # 台帳の値
    observed: str | None  # Config から読み取った値

    @property
    def differs(self) -> bool:
        current, observed = (self.current or ""), (self.observed or "")
        if current == observed:
            return False
        # running-config の `version 15.2` のように、Config 側の情報が粗いだけの場合がある。
        # これを差分として扱うと、適用したときに台帳の情報が劣化してしまう。
        return not (current and observed and current.startswith(observed))


@dataclass
class UnresolvedNeighbor:
    """台帳に見つからない隣接先。記載漏れか、未管理の機器の可能性がある。"""

    local_device: str
    local_interface: str
    remote_hostname: str
    remote_platform: str | None = None


@dataclass
class IngestResult:
    filename: str
    vendor: Vendor
    hostname: str | None
    device_id: int | None
    device_hostname: str | None
    matched: bool  # 台帳の機器と結び付いたか
    unchanged: bool  # 前回と同じ内容だったか
    snapshot_id: int | None
    interface_count: int
    neighbor_count: int
    vlan_count: int
    links_created: int = 0
    diffs: list[FieldDiff] = field(default_factory=list)
    applied_fields: list[str] = field(default_factory=list)
    unresolved_neighbors: list[UnresolvedNeighbor] = field(default_factory=list)
    message: str | None = None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_device(db: Session, hostname: str | None) -> Device | None:
    if not hostname:
        return None
    key = normalize_hostname(hostname)
    for device in db.scalars(select(Device)):
        if normalize_hostname(device.hostname) == key:
            return device
    return None


def compute_diffs(device: Device | None, parsed: ParsedConfig) -> list[FieldDiff]:
    """台帳の値と Config の観測値を並べる。"""
    observed = {
        "vendor": configparse.VENDOR_NAMES.get(parsed.vendor),
        "model": parsed.model,
        "serial_number": parsed.serial_number,
        "os_name": parsed.os_name,
        "os_version": parsed.os_version,
    }
    diffs: list[FieldDiff] = []
    for name in INVENTORY_FIELDS:
        value = observed.get(name)
        if value is None:
            continue  # 読み取れなかった項目は比較しない
        diffs.append(
            FieldDiff(
                field=name,
                label=FIELD_LABELS[name],
                current=getattr(device, name, None) if device else None,
                observed=value,
            )
        )
    return diffs


def ingest(
    db: Session,
    filename: str,
    text: str,
    *,
    device_id: int | None = None,
    apply_inventory: bool = False,
    commit: bool = True,
) -> IngestResult:
    """1ファイル分を取り込む。apply_inventory=True のとき台帳へ反映する。"""
    parsed = configparse.parse(text)
    device = db.get(Device, device_id) if device_id else _find_device(db, parsed.hostname)

    result = IngestResult(
        filename=filename,
        vendor=parsed.vendor,
        hostname=parsed.hostname,
        device_id=device.id if device else None,
        device_hostname=device.hostname if device else None,
        matched=device is not None,
        unchanged=False,
        snapshot_id=None,
        interface_count=len(parsed.interfaces),
        neighbor_count=len(parsed.neighbors),
        vlan_count=len(parsed.vlans),
        diffs=compute_diffs(device, parsed),
    )

    if device is None:
        result.message = (
            f"ホスト名「{parsed.hostname}」に一致する機器が台帳にありません。"
            "内容は保存したので、機器を登録してから紐付けられます。"
            if parsed.hostname
            else "ホスト名を読み取れませんでした。機器を選んで取り込んでください。"
        )

    digest = _digest(text)
    latest = _latest_snapshot(db, device.id if device else None, parsed.hostname)
    if latest is not None and latest.sha256 == digest:
        result.unchanged = True
        result.snapshot_id = latest.id
        result.message = "前回の取り込みと同じ内容のため、世代は追加していません。"
        return result

    with audit_context(ChangeSource.SYSTEM, note=f"Config取り込み: {filename}"):
        snapshot = ConfigSnapshot(
            device_id=device.id if device else None,
            hostname=parsed.hostname,
            filename=filename,
            content=text,
            sha256=digest,
            vendor=str(parsed.vendor),
            parsed_os_name=parsed.os_name,
            parsed_os_version=parsed.os_version,
            parsed_model=parsed.model,
            parsed_serial=parsed.serial_number,
            interface_count=len(parsed.interfaces),
            neighbor_count=len(parsed.neighbors),
        )
        db.add(snapshot)
        db.flush()
        result.snapshot_id = snapshot.id

        if device is not None:
            _sync_interfaces(db, device, parsed)

            if apply_inventory:
                for diff in result.diffs:
                    if diff.differs:
                        setattr(device, diff.field, diff.observed)
                        result.applied_fields.append(diff.label)

            created, unresolved = _sync_links(db, device, parsed)
            result.links_created = created
            result.unresolved_neighbors = unresolved

        if commit:
            db.commit()
        else:
            db.flush()

    return result


def _latest_snapshot(
    db: Session, device_id: int | None, hostname: str | None
) -> ConfigSnapshot | None:
    stmt = select(ConfigSnapshot).order_by(
        ConfigSnapshot.created_at.desc(), ConfigSnapshot.id.desc()
    )
    if device_id is not None:
        stmt = stmt.where(ConfigSnapshot.device_id == device_id)
    elif hostname:
        stmt = stmt.where(ConfigSnapshot.hostname == hostname)
    else:
        return None
    return db.scalars(stmt.limit(1)).first()


def _sync_interfaces(db: Session, device: Device, parsed: ParsedConfig) -> None:
    """最新の Config に載っているインターフェースへ揃える。"""
    existing = {
        i.normalized_name: i
        for i in db.scalars(select(Interface).where(Interface.device_id == device.id))
    }

    seen: set[str] = set()
    for item in parsed.interfaces:
        key = item.key
        seen.add(key)
        interface = existing.get(key)
        if interface is None:
            interface = Interface(device_id=device.id, name=item.name, normalized_name=key)
            db.add(interface)

        interface.name = item.name
        interface.description = item.description
        interface.ip_address = item.ip_address
        interface.vlan = item.vlan
        interface.mode = item.mode
        interface.enabled = item.enabled

    # Config から消えたポートは実機に無いということなので削除する
    for key, interface in existing.items():
        if key not in seen:
            db.delete(interface)


def _sync_links(
    db: Session, device: Device, parsed: ParsedConfig
) -> tuple[int, list[UnresolvedNeighbor]]:
    """隣接情報から結線を作る。相手が台帳に無い場合は未解決として返す。"""
    devices = {normalize_hostname(d.hostname): d for d in db.scalars(select(Device))}

    created = 0
    unresolved: list[UnresolvedNeighbor] = []

    for neighbor in parsed.neighbors:
        remote = devices.get(neighbor.remote_key)
        if remote is None or remote.id == device.id:
            if remote is None:
                unresolved.append(
                    UnresolvedNeighbor(
                        local_device=device.hostname,
                        local_interface=neighbor.local_interface,
                        remote_hostname=neighbor.remote_hostname,
                        remote_platform=neighbor.remote_platform,
                    )
                )
            continue

        link = upsert_link(
            db,
            device.id,
            neighbor.local_interface,
            remote.id,
            neighbor.remote_interface or "",
            protocol=str(neighbor.protocol),
        )
        if link is not None:
            created += 1

    return created, unresolved


def upsert_link(
    db: Session,
    a_device_id: int,
    a_interface: str,
    b_device_id: int,
    b_interface: str,
    *,
    protocol: str | None = None,
    origin: LinkOrigin = LinkOrigin.DISCOVERED,
) -> Link | None:
    """結線を作る。向きが逆の同じ線が既にあれば、両方向で確認できたものとして扱う。

    新規に作った場合はその Link を、既にある場合は None を返す。
    """
    a_key, b_key = normalize(a_interface), normalize(b_interface)

    for link in db.scalars(
        select(Link).where(
            ((Link.a_device_id == a_device_id) & (Link.b_device_id == b_device_id))
            | ((Link.a_device_id == b_device_id) & (Link.b_device_id == a_device_id))
        )
    ):
        forward = (
            link.a_device_id == a_device_id
            and normalize(link.a_interface) == a_key
            and normalize(link.b_interface) == b_key
        )
        reverse = (
            link.a_device_id == b_device_id
            and normalize(link.a_interface) == b_key
            and normalize(link.b_interface) == a_key
        )
        if forward:
            return None
        if reverse:
            # 相手側からも同じ結線が見えた
            link.confirmed_both_ways = True
            return None

    link = Link(
        a_device_id=a_device_id,
        a_interface=a_interface,
        b_device_id=b_device_id,
        b_interface=b_interface,
        origin=origin,
        protocol=protocol,
    )
    db.add(link)
    db.flush()
    return link


def rebuild_links(db: Session) -> tuple[int, list[UnresolvedNeighbor]]:
    """保存済みの最新 Config を読み直して、結線を作り直す。

    後から機器を登録した場合や、パーサを直した場合に使う。
    人が手で引いた線（origin=manual）は消さない。
    """
    for link in db.scalars(select(Link).where(Link.origin == LinkOrigin.DISCOVERED)):
        db.delete(link)
    db.flush()

    created = 0
    unresolved: list[UnresolvedNeighbor] = []

    for device in db.scalars(select(Device)):
        snapshot = _latest_snapshot(db, device.id, None)
        if snapshot is None:
            continue
        parsed = configparse.parse(snapshot.content)
        count, missing = _sync_links(db, device, parsed)
        created += count
        unresolved.extend(missing)

    db.commit()
    return created, unresolved


def diff_snapshots(older: ConfigSnapshot, newer: ConfigSnapshot) -> str:
    """2世代の Config を unified diff で比較する。"""
    return "\n".join(
        difflib.unified_diff(
            older.content.splitlines(),
            newer.content.splitlines(),
            fromfile=f"{older.filename} ({_stamp(older.created_at)})",
            tofile=f"{newer.filename} ({_stamp(newer.created_at)})",
            lineterm="",
        )
    )


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""
