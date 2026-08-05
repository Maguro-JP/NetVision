from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import to_device_detail, to_device_read
from app.db import get_db
from app.models import Device, DeviceStatus, DeviceType, Site
from app.schemas import DeviceCreate, DeviceDetail, DeviceRead, DeviceUpdate
from app.services.lifecycle import Severity, evaluate

router = APIRouter(prefix="/devices", tags=["devices"])

DbSession = Annotated[Session, Depends(get_db)]


def _load(db: Session, device_id: int) -> Device:
    device = db.get(
        Device, device_id, options=[selectinload(Device.contracts), selectinload(Device.site)]
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")
    return device


def _validate_site(db: Session, site_id: int | None) -> None:
    if site_id is not None and db.get(Site, site_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"site {site_id} が存在しません")


@router.get("", response_model=list[DeviceRead])
def list_devices(
    db: DbSession,
    q: str | None = Query(default=None, description="ホスト名 / 型番 / シリアル の部分一致"),
    vendor: str | None = None,
    device_type: DeviceType | None = None,
    device_status: DeviceStatus | None = Query(default=None, alias="status"),
    site_id: int | None = None,
    severity: Severity | None = Query(default=None, description="総合アラート重要度で絞り込む"),
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
) -> list[DeviceRead]:
    stmt = select(Device).options(selectinload(Device.contracts), selectinload(Device.site))

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Device.hostname.ilike(like),
                Device.model.ilike(like),
                Device.serial_number.ilike(like),
            )
        )
    if vendor:
        stmt = stmt.where(Device.vendor == vendor)
    if device_type:
        stmt = stmt.where(Device.device_type == device_type)
    if device_status:
        stmt = stmt.where(Device.status == device_status)
    if site_id is not None:
        stmt = stmt.where(Device.site_id == site_id)

    devices = list(db.scalars(stmt.order_by(Device.hostname)))

    # 重要度は日付から算出するため SQL では絞れない。
    # 想定規模（〜数千台）ではアプリ側で判定して問題ない。
    if severity is not None:
        devices = [d for d in devices if evaluate(d).overall_severity == severity]

    return [to_device_read(d) for d in devices[offset : offset + limit]]


@router.post("", response_model=DeviceDetail, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: DbSession) -> DeviceDetail:
    _validate_site(db, payload.site_id)
    if db.scalar(select(Device).where(Device.hostname == payload.hostname)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"hostname '{payload.hostname}' は登録済みです"
        )

    device = Device(**payload.model_dump())
    db.add(device)
    db.commit()
    db.refresh(device)
    return to_device_detail(device)


@router.get("/{device_id}", response_model=DeviceDetail)
def get_device(device_id: int, db: DbSession) -> DeviceDetail:
    return to_device_detail(_load(db, device_id))


@router.patch("/{device_id}", response_model=DeviceDetail)
def update_device(device_id: int, payload: DeviceUpdate, db: DbSession) -> DeviceDetail:
    device = _load(db, device_id)
    changes = payload.model_dump(exclude_unset=True)

    if "site_id" in changes:
        _validate_site(db, changes["site_id"])
    if "hostname" in changes and changes["hostname"] != device.hostname:
        clash = db.scalar(select(Device).where(Device.hostname == changes["hostname"]))
        if clash:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"hostname '{changes['hostname']}' は登録済みです"
            )

    for key, value in changes.items():
        setattr(device, key, value)

    # 部分更新では既存値との組み合わせで矛盾しうるので、適用後にもう一度検証する
    if device.eos_date and device.eol_date and device.eol_date < device.eos_date:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "eol_date（サポート終了）は eos_date（販売終了）以降である必要があります",
        )

    db.commit()
    db.refresh(device)
    return to_device_detail(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: DbSession) -> None:
    db.delete(_load(db, device_id))
    db.commit()
