from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import to_device_detail, to_device_read
from app.db import get_db
from app.models import Device, DeviceStatus, DeviceType, ScenarioItem, Site
from app.schemas import DeviceCreate, DeviceDetail, DeviceRead, DeviceUpdate
from app.services import configs
from app.services.device_query import DeviceFilters, count_devices, query_devices
from app.services.lifecycle import Severity

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
    incomplete: bool = Query(default=False, description="埋まっていない項目がある機器だけ"),
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
    response: Response = None,  # type: ignore[assignment]
) -> list[DeviceRead]:
    """条件に合う機器を1ページ分返す。

    総数は `X-Total-Count` ヘッダに入れる。16万台の環境では全件を返す
    わけにいかないが、何件あるかが分からないとページも送れないため。
    """
    filters = DeviceFilters(
        q=q,
        vendor=vendor,
        device_type=device_type,
        status=device_status,
        site_id=site_id,
        severity=severity,
        incomplete=incomplete,
    )
    total = count_devices(db, filters)
    devices = query_devices(db, filters, limit=limit, offset=offset)
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return [to_device_read(d) for d in devices]


@router.post("", response_model=DeviceDetail, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: DbSession) -> DeviceDetail:
    _validate_site(db, payload.site_id)
    if db.scalar(select(Device).where(Device.hostname == payload.hostname)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"hostname '{payload.hostname}' は登録済みです"
        )

    device = Device(**payload.model_dump())
    db.add(device)
    db.flush()

    # Config を先に取り込んでいた場合は、ここで引き取らせる。
    # 台帳から作るか Config から作るかは人によって順番が逆になるので、
    # どちらから始めても同じところに着くようにしておく
    configs.adopt_orphan_snapshots(db, device)

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
    device = _load(db, device_id)

    # 将来構成シナリオはこの機器が消えても案として残す（何を検討していたかは device_label に残る）
    for item in db.scalars(select(ScenarioItem).where(ScenarioItem.device_id == device_id)):
        item.device_id = None

    db.delete(device)
    db.commit()
