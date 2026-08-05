from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Site
from app.schemas import SiteCreate, SiteRead, SiteUpdate

router = APIRouter(prefix="/sites", tags=["sites"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[SiteRead])
def list_sites(db: DbSession) -> list[SiteRead]:
    counts = dict(
        db.execute(select(Device.site_id, func.count(Device.id)).group_by(Device.site_id)).all()
    )
    sites = db.scalars(select(Site).order_by(Site.name))
    return [
        SiteRead(
            id=s.id, name=s.name, address=s.address, notes=s.notes, device_count=counts.get(s.id, 0)
        )
        for s in sites
    ]


@router.post("", response_model=SiteRead, status_code=status.HTTP_201_CREATED)
def create_site(payload: SiteCreate, db: DbSession) -> SiteRead:
    if db.scalar(select(Site).where(Site.name == payload.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"site '{payload.name}' は登録済みです")
    site = Site(**payload.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    return SiteRead.model_validate(site)


def _load(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"site {site_id} が見つかりません")
    return site


@router.patch("/{site_id}", response_model=SiteRead)
def update_site(site_id: int, payload: SiteUpdate, db: DbSession) -> SiteRead:
    site = _load(db, site_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(site, key, value)
    db.commit()
    db.refresh(site)
    return SiteRead.model_validate(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(site_id: int, db: DbSession) -> None:
    """拠点を削除する。所属機器は削除せず site_id が null になる。"""
    site = _load(db, site_id)
    for device in db.scalars(select(Device).where(Device.site_id == site_id)):
        device.site_id = None
    db.delete(site)
    db.commit()
