from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device
from app.models.topology import ConfigSnapshot, Interface
from app.schemas.topology import (
    ConfigDiffRead,
    ConfigSnapshotDetail,
    ConfigSnapshotRead,
    IngestResultRead,
    InterfaceRead,
)
from app.services import configs as service
from app.services.configparse import VENDOR_LABELS, Vendor

router = APIRouter(prefix="/configs", tags=["configs"])

DbSession = Annotated[Session, Depends(get_db)]

MAX_CONFIG_BYTES = 10 * 1024 * 1024
CONFIG_ENCODINGS = ("utf-8-sig", "utf-8", "cp932")


def _decode(content: bytes) -> str:
    for encoding in CONFIG_ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 制御文字が混ざった show tech なども取りこぼさない
    return content.decode("utf-8", errors="replace")


def _to_read(result: service.IngestResult) -> IngestResultRead:
    return IngestResultRead(
        filename=result.filename,
        vendor=result.vendor,
        vendor_label=VENDOR_LABELS.get(result.vendor, str(result.vendor)),
        hostname=result.hostname,
        device_id=result.device_id,
        device_hostname=result.device_hostname,
        matched=result.matched,
        created=result.created,
        unchanged=result.unchanged,
        snapshot_id=result.snapshot_id,
        interface_count=result.interface_count,
        neighbor_count=result.neighbor_count,
        vlan_count=result.vlan_count,
        links_created=result.links_created,
        diffs=[
            {
                "field": d.field,
                "label": d.label,
                "current": d.current,
                "observed": d.observed,
                "differs": d.differs,
            }
            for d in result.diffs
        ],
        applied_fields=result.applied_fields,
        unresolved_neighbors=[
            {
                "local_device": n.local_device,
                "local_interface": n.local_interface,
                "remote_hostname": n.remote_hostname,
                "remote_platform": n.remote_platform,
            }
            for n in result.unresolved_neighbors
        ],
        message=result.message,
    )


@router.post("/upload", response_model=list[IngestResultRead])
async def upload_configs(
    db: DbSession,
    files: list[UploadFile] = File(description="config / show 出力のテキストファイル（複数可）"),
    device_id: int | None = Form(default=None, description="ホスト名で判別できない場合に指定"),
    apply_inventory: bool = Form(
        default=False, description="読み取った型番・バージョンを台帳へ反映するか"
    ),
    create_missing: bool = Form(
        default=True, description="台帳に無いホスト名なら機器を新規作成するか"
    ),
) -> list[IngestResultRead]:
    """Config を取り込み、機器・インターフェース・隣接情報を抽出する。

    紐付けはファイル名ではなく Config の中のホスト名で決める。ファイル名は
    現場で自由に付く（日付や作業番号が入る）ので、そこに合わせろという作りは
    道具の都合を人に押し付けることになる。

    既定で、台帳に無いホスト名は新しい機器として登録する。取り込んだのに
    機器一覧に何も出てこないのでは、道具として使えないため。

    既存の値の上書きだけは既定で行わない（apply_inventory）。無い物を足すのと、
    人が入れた値を書き換えるのとでは、間違えたときの取り返しが違う。
    """
    if device_id is not None and db.get(Device, device_id) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"device {device_id} が存在しません"
        )
    if device_id is not None and len(files) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "機器を指定する場合はファイルを1つずつ取り込んでください",
        )

    results: list[IngestResultRead] = []
    for upload in files:
        content = await upload.read()
        if len(content) > MAX_CONFIG_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{upload.filename}: ファイルサイズが大きすぎます（上限 10MB）",
            )
        results.append(
            _to_read(
                service.ingest(
                    db,
                    upload.filename or "config.txt",
                    _decode(content),
                    device_id=device_id,
                    apply_inventory=apply_inventory,
                    create_missing=create_missing,
                )
            )
        )
    return results


@router.post("/preview", response_model=IngestResultRead)
async def preview_config(
    db: DbSession,
    file: UploadFile = File(description="取り込み内容を確認するファイル"),
) -> IngestResultRead:
    """保存せずに、何が読み取れるかだけを返す。"""
    content = await file.read()
    text = _decode(content)

    parsed = service.configparse.parse(text)
    device = service._find_device(db, parsed.hostname)
    result = service.IngestResult(
        filename=file.filename or "config.txt",
        vendor=parsed.vendor,
        hostname=parsed.hostname,
        device_id=device.id if device else None,
        device_hostname=device.hostname if device else None,
        matched=device is not None,
        created=False,
        unchanged=False,
        snapshot_id=None,
        interface_count=len(parsed.interfaces),
        neighbor_count=len(parsed.neighbors),
        vlan_count=len(parsed.vlans),
        diffs=service.compute_diffs(device, parsed),
        message=None if device else "台帳に一致する機器が見つかりません",
    )
    return _to_read(result)


@router.get("", response_model=list[ConfigSnapshotRead])
def list_snapshots(
    db: DbSession,
    device_id: int | None = None,
    limit: int = Query(default=100, le=500),
) -> list[ConfigSnapshot]:
    stmt = select(ConfigSnapshot).order_by(
        ConfigSnapshot.created_at.desc(), ConfigSnapshot.id.desc()
    )
    if device_id is not None:
        stmt = stmt.where(ConfigSnapshot.device_id == device_id)
    return list(db.scalars(stmt.limit(limit)))


def _load(db: Session, snapshot_id: int) -> ConfigSnapshot:
    snapshot = db.get(ConfigSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"config {snapshot_id} が見つかりません")
    return snapshot


@router.get("/{snapshot_id}", response_model=ConfigSnapshotDetail)
def get_snapshot(snapshot_id: int, db: DbSession) -> ConfigSnapshot:
    return _load(db, snapshot_id)


@router.get("/{snapshot_id}/diff", response_model=ConfigDiffRead)
def diff_snapshot(
    snapshot_id: int,
    db: DbSession,
    against: int | None = Query(default=None, description="比較対象。省略時は1つ前の世代"),
) -> ConfigDiffRead:
    """2世代の Config を比較する。変更追跡や監査の証跡に使える。"""
    newer = _load(db, snapshot_id)

    if against is not None:
        older = _load(db, against)
    else:
        stmt = (
            select(ConfigSnapshot)
            .where(ConfigSnapshot.device_id == newer.device_id, ConfigSnapshot.id < newer.id)
            .order_by(ConfigSnapshot.id.desc())
            .limit(1)
        )
        found = db.scalars(stmt).first()
        if found is None:
            return ConfigDiffRead(from_id=newer.id, to_id=newer.id, diff="", changed=False)
        older = found

    if older.id > newer.id:
        older, newer = newer, older

    diff = service.diff_snapshots(older, newer)
    return ConfigDiffRead(from_id=older.id, to_id=newer.id, diff=diff, changed=bool(diff))


@router.delete("/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snapshot(snapshot_id: int, db: DbSession) -> None:
    db.delete(_load(db, snapshot_id))
    db.commit()


@router.get("/devices/{device_id}/interfaces", response_model=list[InterfaceRead])
def list_interfaces(device_id: int, db: DbSession) -> list[Interface]:
    """Config から抽出したインターフェース一覧。"""
    if db.get(Device, device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")
    return list(
        db.scalars(
            select(Interface)
            .where(Interface.device_id == device_id)
            .order_by(Interface.normalized_name)
        )
    )


@router.get("/meta/vendors", response_model=dict[str, str])
def list_vendors() -> dict[str, str]:
    """対応しているベンダー（判別結果の表示名）。"""
    return {
        str(vendor): label
        for vendor, label in VENDOR_LABELS.items()
        if vendor is not Vendor.UNKNOWN
    }
