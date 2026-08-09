from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ConfigPlan, Device
from app.schemas.config_plans import (
    ConfigPlanCreate,
    ConfigPlanRead,
    ConfigPlanUpdate,
    DiffPreviewRequest,
    DiffRead,
    PlanOutputRead,
)
from app.services import config_plans as service
from app.services.api_helpers import diff_read
from app.services.configdiff import DiffResult, build

router = APIRouter(tags=["config-plans"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_read(plan: ConfigPlan) -> ConfigPlanRead:
    read = ConfigPlanRead.model_validate(plan, from_attributes=True)
    read.device_hostname = plan.device.hostname if plan.device else None
    return read


def _diff_read(diff: DiffResult) -> DiffRead:
    return diff_read(diff)


def _get_plan(db: Session, plan_id: int) -> ConfigPlan:
    plan = db.get(ConfigPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の計画が見つかりません")
    return plan


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/devices/{device_id}/design-config", response_model=dict[str, object])
def design_config(device_id: int, db: DbSession) -> dict[str, object]:
    """図に描いた構成から、その機器の想定 Config を組み立てて返す。

    新規構築やテスト環境で、**図を描いてから Config を書き起こす**手間を減らすため。
    図に無い情報（アクセスポート・経路・ACL）は起こせないので、
    その箇所は「要記入」と分かる形で残す。

    ここでは保存しない。計画として残すときは Config計画を
    `seed_from=design` で作る。
    """
    from app.services import design

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")

    plan = design.collect(db, device)
    return {
        "device_id": device.id,
        "hostname": device.hostname,
        "body": design.build(db, device),
        "link_count": len(plan.links),
        "vlan_count": len(plan.vlans),
        # 図から起こせた材料を返す。何を根拠に生成したかが見えないと確認できない
        "links": [
            {
                "local_interface": link.local_interface,
                "peer_hostname": link.peer_hostname,
                "peer_interface": link.peer_interface,
                "infrastructure": link.is_infrastructure,
            }
            for link in plan.links
        ],
    }


@router.get("/config-plans", response_model=list[ConfigPlanRead])
def list_plans(db: DbSession, device_id: int | None = None) -> list[ConfigPlanRead]:
    stmt = select(ConfigPlan).order_by(ConfigPlan.updated_at.desc(), ConfigPlan.id.desc())
    if device_id is not None:
        stmt = stmt.where(ConfigPlan.device_id == device_id)
    return [_to_read(p) for p in db.scalars(stmt)]


@router.get("/devices/{device_id}/config-plans", response_model=list[ConfigPlanRead])
def list_for_device(device_id: int, db: DbSession) -> list[ConfigPlanRead]:
    return list_plans(db, device_id=device_id)


@router.post("/config-plans", response_model=ConfigPlanRead, status_code=status.HTTP_201_CREATED)
def create_plan(payload: ConfigPlanCreate, db: DbSession) -> ConfigPlanRead:
    device = db.get(Device, payload.device_id)
    if device is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "対象の機器が見つかりません")

    body = payload.body
    if not body:
        try:
            body = service.seed_body(db, device, payload.seed_from, payload.template_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    base = payload.base_snapshot_id
    if base is None:
        snapshot = service.latest_snapshot(db, device.id)
        base = snapshot.id if snapshot else None

    plan = ConfigPlan(
        device_id=device.id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        body=body,
        vendor=payload.vendor,
        scenario_id=payload.scenario_id,
        base_snapshot_id=base,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _to_read(plan)


@router.patch("/config-plans/{plan_id}", response_model=ConfigPlanRead)
def update_plan(plan_id: int, payload: ConfigPlanUpdate, db: DbSession) -> ConfigPlanRead:
    plan = _get_plan(db, plan_id)
    changes = payload.model_dump(exclude_unset=True)

    if "body" in changes and changes["body"] != plan.body:
        # 本文が変わったら版を上げる。どの版の差分を渡したかを追えるようにする
        plan.revision += 1

    for field, value in changes.items():
        setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return _to_read(plan)


@router.delete("/config-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(plan_id: int, db: DbSession) -> Response:
    db.delete(_get_plan(db, plan_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 成果物
# ---------------------------------------------------------------------------


@router.get("/config-plans/{plan_id}/outputs", response_model=PlanOutputRead)
def plan_outputs(plan_id: int, db: DbSession) -> PlanOutputRead:
    """現行フル・将来フル・差分をまとめて返す。"""
    plan = _get_plan(db, plan_id)
    result = service.outputs(db, plan)

    return PlanOutputRead(
        plan_id=plan.id,
        device_hostname=plan.device.hostname if plan.device else None,
        vendor=result.vendor,
        base_snapshot_id=result.base_snapshot_id,
        base_is_stale=result.base_is_stale,
        notes=result.notes,
        current_full=result.current_full,
        future_full=result.future_full,
        diff=_diff_read(result.diff),
    )


def _as_text(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/config-plans/{plan_id}/download/{kind}")
def download(plan_id: int, kind: str, db: DbSession) -> Response:
    """成果物をファイルで落とす。

    - `current` … 現行フル（機器故障時の復旧用）
    - `future`  … 将来フル（更改後の機器にそのまま投入する）
    - `diff`    … 差分（既存機器に当てる投入コマンド）
    - `rollback`… 切り戻し
    """
    plan = _get_plan(db, plan_id)
    result = service.outputs(db, plan)
    host = (plan.device.hostname if plan.device else None) or f"plan{plan.id}"

    bodies = {
        "current": (result.current_full, f"{host}-current.txt"),
        "future": (result.future_full, f"{host}-future-r{plan.revision}.txt"),
        "diff": (result.diff.script, f"{host}-diff-r{plan.revision}.txt"),
        "rollback": (result.diff.rollback_script, f"{host}-rollback-r{plan.revision}.txt"),
    }
    if kind not in bodies:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その種別の出力はありません")

    body, filename = bodies[kind]
    if kind == "diff" and not result.diff.verified:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "差分が将来の config に収束しないため出力できません。"
            "画面で収束しなかった箇所を確認してください",
        )
    return _as_text(body, filename)


@router.post("/config-plans/preview", response_model=DiffRead)
def preview(payload: DiffPreviewRequest) -> DiffRead:
    """計画を作らずに、2つの config をその場で比べる。"""
    return _diff_read(build(payload.current, payload.target, payload.vendor))
