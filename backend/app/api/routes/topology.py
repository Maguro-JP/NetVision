from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Vlan
from app.models.topology import Link, LinkOrigin
from app.schemas.topology import (
    LinkCreate,
    LinkRead,
    LinkUpdate,
    RebuildResult,
    SavePositions,
    TopologyGraphRead,
    TopologyStatsRead,
    VlanCatalogCreate,
    VlanCatalogRead,
    VlanCatalogWrite,
    VlanRead,
    VrfRead,
)
from app.services import configs as config_service
from app.services import topology as service
from app.services import topology_export
from app.services import vlans as vlan_service

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


@router.get("/vlans", response_model=list[VlanRead])
def list_vlans(db: DbSession) -> list[VlanRead]:
    """定義されている VLAN を、載っている機器つきで返す。

    「VLAN 20 はどこまで伸びているのか」を見るための一覧。
    """
    return service.vlan_summary(db)


@router.get("/vlans/catalog", response_model=list[VlanCatalogRead])
def list_vlan_catalog(db: DbSession) -> list[Vlan]:
    """VLAN 台帳（人が用途を書く側）を返す。"""
    return vlan_service.list_vlans(db)


@router.put("/vlans/catalog/{vlan_id}", response_model=VlanCatalogRead)
def upsert_vlan_catalog(vlan_id: int, payload: VlanCatalogWrite, db: DbSession) -> Vlan:
    """VLAN の名前・用途を登録/変更する。

    ここに書いた内容は Config を取り込み直しても上書きされない。
    実機の設定と食い違っていれば、それは実機側が古いという情報になる。
    """
    if not 1 <= vlan_id <= 4094:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "VLAN は 1〜4094 です")
    return vlan_service.upsert(db, vlan_id, payload.model_dump(exclude_unset=True))


@router.post("/vlans/catalog", response_model=VlanCatalogRead, status_code=status.HTTP_201_CREATED)
def create_vlan_catalog(payload: VlanCatalogCreate, db: DbSession) -> Vlan:
    values = payload.model_dump(exclude_unset=True)
    vlan_id = values.pop("vlan_id")
    return vlan_service.upsert(db, vlan_id, values)


@router.delete("/vlans/catalog/{vlan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vlan_catalog(vlan_id: int, db: DbSession) -> None:
    """台帳から消すだけで、実機の設定や観測値には触らない。"""
    if not vlan_service.delete(db, vlan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"VLAN {vlan_id} は台帳にありません")


@router.post("/vlans/catalog/adopt", response_model=dict[str, int])
def adopt_observed_vlans(db: DbSession, overwrite: bool = False) -> dict[str, int]:
    """実機に書いてある VLAN 名を台帳へ下書きとして取り込む。

    数が多い現場で台帳を1から手で埋めるのは現実的ではないので、まず実機の名前で
    埋めてから用途を書き足す。既に書いてある行は既定では触らない。
    """
    result = vlan_service.adopt_observed(db, overwrite=overwrite)
    return {"created": result.created, "updated": result.updated}


@router.get("/vrfs", response_model=list[VrfRead])
def list_vrfs(db: DbSession) -> list[VrfRead]:
    """機器の中に切られた仮想ルータを、名前ごとにまとめて返す。"""
    return service.vrf_summary(db)


@router.get("/export.svg", response_class=Response)
def export_topology(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    title: str = "ネットワーク構成図",
    vlan: int | None = Query(default=None, description="この VLAN を持つ機器だけを描く"),
    vrf: str | None = Query(default=None, description="この VRF に属する機器だけを描く"),
    style: str = Query(
        default="detail",
        description="detail = 型番や状態つき / simple = 機器名と結線だけ",
    ),
    port_style: str = Query(
        default="short",
        description="ポート名の出し方。full = そのまま / short = 略記 / number = 番号だけ",
    ),
    along_line: bool = Query(default=False, description="ポート名を線に沿わせて回転する"),
) -> Response:
    """構成図を SVG で書き出す。

    設計書や報告書に貼るための出力。ベクタなので拡大・印刷しても潰れず、
    Word / PowerPoint / Excel はそのまま挿入できる（Office 2016 以降）。
    画面で整えた配置をそのまま使う。

    `style=simple` は機器名と線だけの図。作業手順書や現場での確認に使う想定で、
    型番・IP・保守状態・VLAN といった付帯情報を一切載せない。

    ポート名は**それぞれの機器側**に置く。1本の線に2つ並べると「左が上位」の
    ような規則を覚える必要が出て、図の向きが変わると逆に読んでしまうため。
    """
    svg = topology_export.export_svg(
        db,
        layer,
        title=title,
        vlan=vlan,
        vrf=vrf,
        style=style,
        port_style=port_style,
        along_line=along_line,
    )
    suffix = f"-vlan{vlan}" if vlan else ""
    if vrf:
        suffix += f"-{vrf}"
    if style == "simple":
        suffix += "-simple"
    filename = f"topology-{layer}{suffix}.svg"
    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
