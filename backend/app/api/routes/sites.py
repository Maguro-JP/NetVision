from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Site
from app.schemas import SiteCreate, SiteRead, SiteUpdate

router = APIRouter(prefix="/sites", tags=["sites"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_read(site: Site, device_count: int = 0) -> SiteRead:
    return SiteRead(
        id=site.id,
        code=site.code,
        name=site.name,
        address=site.address,
        status=site.status,
        opened_on=site.opened_on,
        closed_on=site.closed_on,
        notes=site.notes,
        device_count=device_count,
    )


def _ensure_unique_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(Site).where(Site.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Site.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(status.HTTP_409_CONFLICT, f"拠点名 '{name}' は登録済みです")


def _ensure_unique_code(db: Session, code: str | None, *, exclude_id: int | None = None) -> None:
    if not code:
        return
    stmt = select(Site).where(Site.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Site.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(status.HTTP_409_CONFLICT, f"拠点ID '{code}' は登録済みです")


@router.get("", response_model=list[SiteRead])
def list_sites(db: DbSession) -> list[SiteRead]:
    counts = dict(
        db.execute(select(Device.site_id, func.count(Device.id)).group_by(Device.site_id)).all()
    )
    # 拠点IDがあるものはそれを先に、無ければ名前順。台帳として眺めやすい
    sites = list(db.scalars(select(Site)))
    sites.sort(key=lambda s: (s.code is None, (s.code or "").casefold(), s.name.casefold()))
    return [_to_read(s, counts.get(s.id, 0)) for s in sites]


@router.post("", response_model=SiteRead, status_code=status.HTTP_201_CREATED)
def create_site(payload: SiteCreate, db: DbSession) -> SiteRead:
    _ensure_unique_name(db, payload.name)
    _ensure_unique_code(db, payload.code)
    site = Site(**payload.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    return _to_read(site)


def _load(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"site {site_id} が見つかりません")
    return site


@router.patch("/{site_id}", response_model=SiteRead)
def update_site(site_id: int, payload: SiteUpdate, db: DbSession) -> SiteRead:
    site = _load(db, site_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        _ensure_unique_name(db, changes["name"], exclude_id=site_id)
    if "code" in changes:
        _ensure_unique_code(db, changes["code"], exclude_id=site_id)
    for key, value in changes.items():
        setattr(site, key, value)
    db.commit()
    db.refresh(site)
    count = db.scalar(select(func.count(Device.id)).where(Device.site_id == site_id)) or 0
    return _to_read(site, count)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(site_id: int, db: DbSession) -> None:
    """拠点を削除する。所属機器は削除せず site_id が null になる。"""
    site = _load(db, site_id)
    for device in db.scalars(select(Device).where(Device.site_id == site_id)):
        device.site_id = None
    db.delete(site)
    db.commit()
