from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import to_device_detail, to_device_read
from app.db import get_db
from app.models import Device, DeviceStatus, DeviceType, ScenarioItem, Site
from app.schemas import DeviceCreate, DeviceDetail, DeviceRead, DeviceUpdate
from app.services import configs, linkimport
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"site {site_id} が存在しません")


def _resolve_site_code(db: Session, code: str | None) -> int | None:
    """拠点ID（人が決めた code）で拠点を特定する。無ければ作らない（IDは人が決めるもの）。"""
    wanted = (code or "").strip()
    if not wanted:
        return None
    site = db.scalar(select(Site).where(Site.code == wanted))
    if site is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"拠点ID '{wanted}' の拠点がありません。先に拠点台帳へ登録してください",
        )
    return site.id


def _resolve_site_name(db: Session, name: str | None) -> int | None:
    """拠点を名前で受け取る。台帳に無い名前ならその場で作る。

    拠点を先に登録しないと機器を登録できないのでは、試験や仮の入力で手が
    止まる。作業の順番を道具の都合で決めさせないための入口。

    表記ゆれで拠点が二重にできないよう、前後の空白を落として大文字小文字を
    無視して突き合わせる。名前が拠点IDと一致する既存拠点があれば、そちらを使う。
    """
    wanted = (name or "").strip()
    if not wanted:
        return None

    by_code = db.scalar(select(Site).where(Site.code == wanted))
    if by_code is not None:
        return by_code.id

    for site in db.scalars(select(Site)):
        if site.name.strip().casefold() == wanted.casefold():
            return site.id

    site = Site(name=wanted)
    db.add(site)
    db.flush()
    return site.id


def _ensure_unique_device_code(
    db: Session, code: str | None, *, exclude_id: int | None = None
) -> None:
    if not code:
        return
    stmt = select(Device).where(Device.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Device.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(status.HTTP_409_CONFLICT, f"管理ID '{code}' は登録済みです")


@router.get("", response_model=list[DeviceRead])
def list_devices(
    db: DbSession,
    q: str | None = Query(default=None, description="どの項目でも部分一致で探す"),
    hostname: str | None = Query(default=None, description="ホスト名の部分一致"),
    vendor: str | None = Query(default=None, description="メーカーの部分一致"),
    model: str | None = Query(default=None, description="型番の部分一致"),
    serial_number: str | None = Query(default=None, description="シリアルの部分一致"),
    os_version: str | None = Query(default=None, description="バージョンの部分一致"),
    mgmt_ip: str | None = Query(default=None, description="管理IPの部分一致"),
    rack: str | None = Query(default=None, description="ラック / Uの部分一致"),
    notes: str | None = Query(default=None, description="メモの部分一致"),
    product: str | None = Query(default=None, description="メーカーまたは型番の部分一致"),
    site_name: str | None = Query(default=None, description="拠点名の部分一致"),
    maintenance: str | None = Query(default=None, description="保守期限の部分一致"),
    eol: str | None = Query(default=None, description="EoLの部分一致"),
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
        hostname=hostname,
        vendor=vendor,
        model=model,
        serial_number=serial_number,
        os_version=os_version,
        mgmt_ip=mgmt_ip,
        rack=rack,
        notes=notes,
        product=product,
        site_name=site_name,
        maintenance=maintenance,
        eol=eol,
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
    _ensure_unique_device_code(db, payload.code)

    values = payload.model_dump()
    site_code = values.pop("site_code", None)
    site_name = values.pop("site_name", None)
    # 拠点IDがあればそれを優先。名前は従来どおりその場で拠点を起こせる
    if site_code:
        values["site_id"] = _resolve_site_code(db, site_code)
    elif site_name:
        values["site_id"] = _resolve_site_name(db, site_name)

    device = Device(**values)
    db.add(device)
    db.flush()

    # Config を先に取り込んでいた場合は、ここで引き取らせる。
    # 台帳から作るか Config から作るかは人によって順番が逆になるので、
    # どちらから始めても同じところに着くようにしておく
    configs.adopt_orphan_snapshots(db, device)
    # 結線表を先に取り込んでいた場合も、この機器の分をここで結ぶ
    linkimport.resolve_pending(db)

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

    site_code = changes.pop("site_code", None)
    site_name = changes.pop("site_name", None)
    if site_code is not None:
        changes["site_id"] = _resolve_site_code(db, site_code) if site_code else None
    elif site_name is not None:
        changes["site_id"] = _resolve_site_name(db, site_name)

    if "site_id" in changes:
        _validate_site(db, changes["site_id"])
    if "hostname" in changes and changes["hostname"] != device.hostname:
        clash = db.scalar(select(Device).where(Device.hostname == changes["hostname"]))
        if clash:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"hostname '{changes['hostname']}' は登録済みです"
            )
    if "code" in changes:
        _ensure_unique_device_code(db, changes["code"], exclude_id=device_id)

    for key, value in changes.items():
        setattr(device, key, value)

    # 部分更新では既存値との組み合わせで矛盾しうるので、適用後にもう一度検証する
    if device.eos_date and device.eol_date and device.eol_date < device.eos_date:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
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
