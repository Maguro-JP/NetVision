from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device
from app.models.topology import Link, LinkOrigin
from app.schemas.topology import (
    LinkCreate,
    LinkRead,
    LinkUpdate,
    RebuildResult,
    SavePositions,
    TopologyGraphRead,
    TopologyStatsRead,
)
from app.services import configs as config_service
from app.services import topology as service

router = APIRouter(prefix="/topology", tags=["topology"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=TopologyGraphRead)
def get_topology(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER, description="physical / l2 / l3 など"),
    include_retired: bool = False,
) -> TopologyGraphRead:
    """図に描くためのノードと結線を返す。位置は保存済みのものを優先する。"""
    graph = service.get_graph(db, layer, include_retired=include_retired)
    return TopologyGraphRead.model_validate(graph, from_attributes=True)


@router.get("/stats", response_model=TopologyStatsRead)
def get_stats(db: DbSession, layer: str = service.DEFAULT_LAYER) -> TopologyStatsRead:
    return TopologyStatsRead.model_validate(service.get_stats(db, layer), from_attributes=True)


@router.put("/positions", response_model=dict[str, int])
def save_positions(payload: SavePositions, db: DbSession) -> dict[str, int]:
    """人が動かしたノード位置を保存する。"""
    known = {d.id for d in db.scalars(select(Device))}
    unknown = [p.device_id for p in payload.positions if p.device_id not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"存在しない機器が含まれています: {unknown}",
        )

    saved = service.save_positions(
        db, payload.layer, [(p.device_id, p.x, p.y) for p in payload.positions]
    )
    return {"saved": saved}


@router.delete("/positions", response_model=dict[str, int])
def reset_positions(db: DbSession, layer: str = service.DEFAULT_LAYER) -> dict[str, int]:
    """配置をリセットして自動レイアウトに戻す。"""
    return {"reset": service.reset_positions(db, layer)}


@router.post("/rebuild", response_model=RebuildResult)
def rebuild(db: DbSession) -> RebuildResult:
    """保存済みの Config を読み直して結線を作り直す。手で引いた線は残す。"""
    created, unresolved = config_service.rebuild_links(db)
    return RebuildResult(
        links_created=created,
        unresolved_neighbors=[
            {
                "local_device": n.local_device,
                "local_interface": n.local_interface,
                "remote_hostname": n.remote_hostname,
                "remote_platform": n.remote_platform,
            }
            for n in unresolved
        ],
    )


@router.get("/links", response_model=list[LinkRead])
def list_links(db: DbSession) -> list[Link]:
    return list(db.scalars(select(Link).order_by(Link.id)))


@router.post("/links", response_model=LinkRead, status_code=status.HTTP_201_CREATED)
def create_link(payload: LinkCreate, db: DbSession) -> Link:
    """人が図に引いた線を登録する。"""
    if payload.a_device_id == payload.b_device_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "同じ機器同士は結線できません")

    for device_id in (payload.a_device_id, payload.b_device_id):
        if db.get(Device, device_id) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"device {device_id} が存在しません"
            )

    link = config_service.upsert_link(
        db,
        payload.a_device_id,
        payload.a_interface,
        payload.b_device_id,
        payload.b_interface,
        origin=LinkOrigin.MANUAL,
    )
    if link is None:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "同じ結線が既に登録されています")

    link.label = payload.label
    db.commit()
    db.refresh(link)
    return link


def _load(db: Session, link_id: int) -> Link:
    link = db.get(Link, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"link {link_id} が見つかりません")
    return link


@router.patch("/links/{link_id}", response_model=LinkRead)
def update_link(link_id: int, payload: LinkUpdate, db: DbSession) -> Link:
    link = _load(db, link_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(link, key, value)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(link_id: int, db: DbSession) -> None:
    db.delete(_load(db, link_id))
    db.commit()
