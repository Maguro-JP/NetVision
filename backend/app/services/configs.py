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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Device, DeviceTunnel, DeviceVlan, DeviceVrf
from app.models.change_log import ChangeSource
from app.models.topology import ConfigSnapshot, Interface, Link, LinkOrigin
from app.services import configparse
from app.services.audit import audit_context
from app.services.configparse import ParsedConfig, Vendor, normalize, normalize_hostname, vlanlist

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
    created: bool  # 台帳に無かったので新規に作ったか
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


def _find_device(
    db: Session, hostname: str | None, serial_number: str | None = None
) -> Device | None:
    """ホスト名で探し、無ければシリアルで探す。

    機器名（sysname）が変わっても実機は同じ、ということがある。
    シリアルは台帳上も一意なので、そちらが当たれば同一機とみなす。

    以前は全機器を読んで Python で正規化比較していた。16万台では
    Config 1件の取り込みのたびに一覧と同じ待ちが発生する。
    """
    if hostname:
        key = normalize_hostname(hostname)
        if key:
            found = _find_device_by_hostname(db, key)
            if found is not None:
                return found
    serial = (serial_number or "").strip()
    if not serial:
        return None
    return db.scalar(select(Device).where(Device.serial_number == serial))


def _find_device_by_hostname(db: Session, key: str) -> Device | None:
    """正規化後のホスト名で1台引く。FQDN やシリアル付き表記も同じキーになる。"""
    stmt = select(Device).where(
        or_(
            func.lower(Device.hostname) == key,
            Device.hostname.ilike(f"{key}.%"),
            Device.hostname.ilike(f"{key}(%"),
        )
    )
    for device in db.scalars(stmt):
        if normalize_hostname(device.hostname) == key:
            return device
    return None


def _create_from_config(db: Session, parsed: ParsedConfig) -> Device | None:
    """Config から読み取れた値だけで機器を作る。

    台帳が空の状態から実 Config で立ち上げるときの入口。読めなかった項目は
    埋めずに空のままにする（推測で埋めると、後から本物の値が入ったときに
    どちらが正しいか分からなくなる）。

    型番が読めなくても登録する。plain な running-config には型番が入って
    いないことが多く、以前はそれを理由に登録を見送っていた。結果として、
    取り込んだのに機器一覧に何も出てこない——利用者から見れば「何も
    起きなかった」という状態になっていた。

    足りないまま載せる代わりに、何が足りないかは機器一覧で分かるようにする
    （Device.missing_fields）。黙って捨てるより、足りない印を付けて見せる方が
    直しようがある。型番は EoL も脆弱性も引けないので、埋めてもらう必要は
    変わらない。
    """
    if not parsed.hostname:
        return None

    device = Device(
        hostname=parsed.hostname,
        vendor=configparse.VENDOR_NAMES.get(parsed.vendor) or "",
        model=parsed.model or "",
        serial_number=parsed.serial_number,
        os_name=parsed.os_name,
        os_version=parsed.os_version,
    )
    db.add(device)
    db.flush()
    return device


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
    create_missing: bool = False,
    commit: bool = True,
) -> IngestResult:
    """1ファイル分を取り込む。

    - `apply_inventory` … 既存機器の項目を Config の観測値で更新する
    - `create_missing`  … 台帳に無いホスト名なら機器を新規作成する

    既存の値を勝手に上書きしないのが原則だが、まだ無い機器を作るのは上書きでは
    ないので分けてある。台帳が空の状態から実 Config で立ち上げるときに使う。
    """
    parsed = configparse.parse(text)
    device = (
        db.get(Device, device_id)
        if device_id
        else _find_device(db, parsed.hostname, parsed.serial_number)
    )

    created = False
    creation_note: str | None = None
    if device is None and create_missing and parsed.hostname:
        device = _create_from_config(db, parsed)
        created = device is not None
        if device is not None and device.missing_fields:
            creation_note = (
                f"{device.hostname} を台帳に登録しました。"
                f"{'・'.join(device.missing_fields)}が読み取れていません。"
                "型番は EoL や脆弱性を引く鍵になるので、"
                "`show version` の出力も取り込むか、機器一覧から入力してください"
            )

    result = IngestResult(
        filename=filename,
        vendor=parsed.vendor,
        hostname=parsed.hostname,
        device_id=device.id if device else None,
        device_hostname=device.hostname if device else None,
        matched=device is not None,
        created=created,
        unchanged=False,
        snapshot_id=None,
        interface_count=len(parsed.interfaces),
        neighbor_count=len(parsed.neighbors),
        vlan_count=len(parsed.vlans),
        diffs=compute_diffs(device, parsed),
    )

    if creation_note:
        result.message = creation_note
    elif device is None:
        result.message = (
            f"ホスト名「{parsed.hostname}」に一致する機器が台帳にありません。"
            "内容は保存したので、機器を登録してから紐付けられます。"
            if parsed.hostname
            else "ホスト名を読み取れませんでした。機器を選んで取り込んでください。"
        )

    digest = _digest(text)
    latest = _latest_snapshot(db, device.id if device else None, parsed.hostname)

    # 同じ内容なら世代は増やさない。増やしても差分が空の履歴が並ぶだけで、
    # あとから「いつ変わったか」を追うときの邪魔になる。
    #
    # ただし、台帳への反映まで止めてはいけない。同じ Config でも、台帳側が
    # 空だったり、あとから機器を登録して紐付いたばかりだったりする。
    # 「同じ内容だから」という理由で更新されないのは、使う側から見れば
    # 反映されないバグにしか見えない。世代を足すかどうかと、読み取った値を
    # 台帳へ反映するかどうかは別の話として扱う。
    result.unchanged = latest is not None and latest.sha256 == digest

    with audit_context(ChangeSource.SYSTEM, note=f"Config取り込み: {filename}"):
        if result.unchanged and latest is not None:
            result.snapshot_id = latest.id
            # 先に取り込んで宙に浮いていた世代を、今回の機器に引き取らせる
            if device is not None and latest.device_id is None:
                latest.device_id = device.id
        else:
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
            _sync_vlans(db, device, parsed)
            _sync_vrfs(db, device, parsed)
            _sync_tunnels(db, device, parsed)
            _sync_interfaces(db, device, parsed)

            if apply_inventory:
                for diff in result.diffs:
                    if diff.differs:
                        setattr(device, diff.field, diff.observed)
                        result.applied_fields.append(diff.label)

            created, unresolved = _sync_links(db, device, parsed)
            result.links_created = created
            result.unresolved_neighbors = unresolved

        if result.unchanged and not result.message:
            result.message = "前回と同じ内容のため、世代は追加していません。" + (
                f"台帳へ反映: {' / '.join(result.applied_fields)}"
                if result.applied_fields
                else "ポート・結線は読み直しました。"
            )

        if commit:
            db.commit()
        else:
            db.flush()

    return result


def adopt_orphan_snapshots(db: Session, device: Device) -> int:
    """先に取り込んであった Config を、あとから登録した機器に引き取らせる。

    順番はどちらもあり得る。台帳を作ってから Config を入れる人もいれば、
    手元の Config をまとめて入れてから機器を整える人もいる。後者のとき、
    取り込み済みの世代が宙に浮いたままだと、機器を登録しても Config が
    付いてこない——取り込み直しが要る、という筋の通らない手順になる。

    引き取るときは、いちばん新しい世代を読み直してポート・VLAN・結線まで
    作る。紐付けだけして中身が空のままでは、紐付いた意味がないため。

    戻り値は引き取った世代の数。
    """
    key = normalize_hostname(device.hostname)
    if not key:
        return 0

    orphans = [
        snapshot
        for snapshot in db.scalars(select(ConfigSnapshot).where(ConfigSnapshot.device_id.is_(None)))
        if snapshot.hostname and normalize_hostname(snapshot.hostname) == key
    ]
    if not orphans:
        return 0

    for snapshot in orphans:
        snapshot.device_id = device.id

    newest = max(orphans, key=lambda s: (s.created_at, s.id))
    parsed = configparse.parse(newest.content)
    _sync_vlans(db, device, parsed)
    _sync_vrfs(db, device, parsed)
    _sync_tunnels(db, device, parsed)
    _sync_interfaces(db, device, parsed)
    _sync_links(db, device, parsed)
    db.flush()
    return len(orphans)


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


def _sync_vlans(db: Session, device: Device, parsed: ParsedConfig) -> None:
    """その機器に定義されている VLAN を揃える。

    図や一覧で「10」とだけ出ても何のセグメントか分からないので、名前も持つ。
    """
    rows = db.scalars(select(DeviceVlan).where(DeviceVlan.device_id == device.id))
    existing = {v.vlan_id: v for v in rows}
    for vlan_id, name in parsed.vlans.items():
        row = existing.pop(vlan_id, None)
        if row is None:
            db.add(DeviceVlan(device_id=device.id, vlan_id=vlan_id, name=name or None))
        elif name and row.name != name:
            row.name = name

    # Config から消えた VLAN は落とす（最新の Config に揃える）
    for row in existing.values():
        db.delete(row)


def _sync_vrfs(db: Session, device: Device, parsed: ParsedConfig) -> None:
    """その機器に切られている仮想ルータ（VRF / VPN インスタンス / VDOM）を揃える。"""
    rows = db.scalars(select(DeviceVrf).where(DeviceVrf.device_id == device.id))
    existing = {v.name: v for v in rows}

    for vrf in parsed.vrfs:
        row = existing.pop(vrf.name, None)
        if row is None:
            db.add(
                DeviceVrf(
                    device_id=device.id, name=vrf.name, rd=vrf.rd, description=vrf.description
                )
            )
            continue
        row.rd = vrf.rd or row.rd
        row.description = vrf.description or row.description

    for row in existing.values():
        db.delete(row)


def _sync_tunnels(db: Session, device: Device, parsed: ParsedConfig) -> None:
    """その機器に張られているトンネルを揃える。論理構成図の材料。"""
    rows = db.scalars(select(DeviceTunnel).where(DeviceTunnel.device_id == device.id))
    existing = {t.name: t for t in rows}

    for tunnel in parsed.tunnels:
        row = existing.pop(tunnel.name, None)
        if row is None:
            db.add(
                DeviceTunnel(
                    device_id=device.id,
                    name=tunnel.name,
                    kind=tunnel.kind,
                    source=tunnel.source,
                    destination=tunnel.destination,
                    vrf=tunnel.vrf,
                    description=tunnel.description,
                )
            )
            continue
        # 最新の Config に合わせる。消えた設定を残すと、無い経路が図に出る
        row.kind = tunnel.kind
        row.source = tunnel.source
        row.destination = tunnel.destination
        row.vrf = tunnel.vrf
        row.description = tunnel.description

    for row in existing.values():
        db.delete(row)


def _trunk_column(item) -> str | None:
    """許可 VLAN を1カラムに畳む。

    `all`（全許可）は番号に展開せずその語のまま入れる。展開してしまうと、
    あとから VLAN が増えたときに古い一覧が残ってしまう。
    """
    if item.trunk_all:
        if item.trunk_excluded:
            return f"all except {vlanlist.format_ranges(item.trunk_excluded)}"
        return "all"
    return vlanlist.format_ranges(item.trunk_vlans) or None


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
        interface.native_vlan = item.native_vlan
        interface.trunk_vlans = _trunk_column(item)
        interface.vrf = item.vrf
        interface.enabled = item.enabled

    # Config から消えたポートは実機に無いということなので削除する
    for key, interface in existing.items():
        if key not in seen:
            db.delete(interface)


def _sync_links(
    db: Session,
    device: Device,
    parsed: ParsedConfig,
    *,
    hostname_index: dict[str, Device] | None = None,
) -> tuple[int, list[UnresolvedNeighbor]]:
    """隣接情報から結線を作る。相手が台帳に無い場合は未解決として返す。

    1件の取り込みでは相手を SQL で引く。全台の再構築（rebuild_links）では
    呼び出し側が作った索引を使い、同じ全件ロードを繰り返さない。
    """
    created = 0
    unresolved: list[UnresolvedNeighbor] = []

    for neighbor in parsed.neighbors:
        if hostname_index is not None:
            remote = hostname_index.get(neighbor.remote_key)
        else:
            remote = _find_device_by_hostname(db, neighbor.remote_key)
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
    hostname_index = {normalize_hostname(d.hostname): d for d in db.scalars(select(Device))}

    for device in hostname_index.values():
        snapshot = _latest_snapshot(db, device.id, None)
        if snapshot is None:
            continue
        parsed = configparse.parse(snapshot.content)
        count, missing = _sync_links(db, device, parsed, hostname_index=hostname_index)
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
    from app.services import localtime

    return localtime.format(value)
