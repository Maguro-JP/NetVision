from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ConfigPlan, Device
from app.schemas.config_plans import (
    ConfigDraftRead,
    ConfigDraftRequest,
    ConfigPlanCreate,
    ConfigPlanRead,
    ConfigPlanUpdate,
    ConfigReviewRequest,
    DiffPreviewRequest,
    DiffRead,
    DraftLineRead,
    DraftSourceRead,
    LlmStatusRead,
    PlanOutputRead,
)
from app.services import config_draft, jobs, llm
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

    新規構築やテスト環境で、図を描いてから Config を書き起こす手間を減らすため。
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


# ---------------------------------------------------------------------------
# 資料を根拠にした下書き
# ---------------------------------------------------------------------------


@router.get("/llm/status", response_model=LlmStatusRead)
def llm_status() -> LlmStatusRead:
    """モデルを置いてあるか。画面に「下書きを作る」を出すかの判断に使う。"""
    return LlmStatusRead.model_validate(llm.status())


class LlmSwitchRequest(BaseModel):
    """モデルを使うかどうかの切り替え。"""

    enabled: bool


@router.post("/llm/enabled", response_model=LlmStatusRead)
def switch_llm(payload: LlmSwitchRequest) -> LlmStatusRead:
    """画面から、モデルを使うかどうかを切り替える。

    規則ベースだけのときと結果を見比べたい、遅いので一時的に止めたい、という
    場面のため。環境変数だと起動し直しになるので、その場で切れるようにする。
    """
    try:
        llm.set_enabled(payload.enabled)
    except RuntimeError as error:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, str(error)) from error
    return LlmStatusRead.model_validate(llm.status())


@router.post("/devices/{device_id}/config-draft", response_model=ConfigDraftRead)
def draft_config(device_id: int, payload: ConfigDraftRequest, db: DbSession) -> ConfigDraftRead:
    """取り込んだ資料を根拠に、投入コマンドの下書きを作る。実機へは投入しない。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    if not payload.request.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "やりたいことを書いてください")

    result = config_draft.draft(db, device, payload.request, base=payload.base)
    return ConfigDraftRead(
        device_id=device.id,
        vendor=result.vendor,
        body=result.body,
        lines=[
            DraftLineRead(text=line.text, grounded=line.grounded, risks=line.risks)
            for line in result.lines
        ],
        sources=[
            DraftSourceRead(
                document_id=source.document_id,
                title=source.title,
                heading=source.heading,
                commands=source.commands[:20],
            )
            for source in result.sources
        ],
        risks=result.risks,
        notes=result.notes,
        model=result.model,
    )


def _draft_read(device: Device, result) -> ConfigDraftRead:
    return ConfigDraftRead(
        device_id=device.id,
        vendor=result.vendor,
        body=result.body,
        lines=[
            DraftLineRead(text=line.text, grounded=line.grounded, risks=line.risks)
            for line in result.lines
        ],
        sources=[
            DraftSourceRead(
                document_id=source.document_id,
                title=source.title,
                heading=source.heading,
                commands=source.commands[:20],
            )
            for source in result.sources
        ],
        risks=result.risks,
        notes=result.notes,
        model=result.model,
    )


@router.post("/devices/{device_id}/config-draft/prompt", response_model=ConfigDraftRead)
def draft_prompt(device_id: int, payload: ConfigDraftRequest, db: DbSession) -> ConfigDraftRead:
    """手元にモデルを置けない場合に、人が AI へ貼るための依頼文を作る。

    資料の該当箇所と現行 Config を材料として並べる。返ってきた設定は
    `/config-draft/review` に貼り戻すと、こちらで検める。
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    if not payload.request.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "やりたいことを書いてください")

    result = config_draft.prompt_for(db, device, payload.request, base=payload.base)
    return _draft_read(device, result)


@router.post("/devices/{device_id}/config-draft/review", response_model=ConfigDraftRead)
def review_draft(device_id: int, payload: ConfigReviewRequest, db: DbSession) -> ConfigDraftRead:
    """よそで書いた設定を、資料と突き合わせて検める。

    どこで書かれたものでも通す道は同じにする。書いた場所によって確かめ方が
    変わると、確かめ漏れがそこに溜まる。
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    if not payload.body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "設定を貼ってください")

    return _draft_read(device, config_draft.review(db, device, payload.request, payload.body))


# ---------------------------------------------------------------------------
# 裏で走らせる（下書きは数十秒かかることがある）
# ---------------------------------------------------------------------------


class JobRead(BaseModel):
    """裏の処理の様子。"""

    id: str
    kind: str
    status: str
    seconds: float
    error: str | None = None
    draft: ConfigDraftRead | None = None


def _job_read(job) -> JobRead:
    return JobRead(
        id=job.id,
        kind=job.kind,
        status=job.status,
        seconds=round(job.seconds, 1),
        error=job.error,
        draft=job.result if isinstance(job.result, ConfigDraftRead) else None,
    )


@router.post("/devices/{device_id}/config-draft/async", response_model=JobRead)
def draft_config_in_background(
    device_id: int, payload: ConfigDraftRequest, db: DbSession
) -> JobRead:
    """下書きを裏で作る。受け付けたらすぐ番号を返す。

    手元のモデルに頼むと CPU では数十秒かかる。そのあいだ画面が固まったように
    見えるのを避ける。様子は `/jobs/{id}` で聞く。
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    if not payload.request.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "やりたいことを書いてください")

    device_id_value = device.id
    request_text = payload.request
    base_text = payload.base

    def work() -> ConfigDraftRead:
        # 裏では、その場限りの接続を使う。画面の要求とつなぎを共有すると、
        # 先に閉じられて途中で落ちる
        from app.db import new_session

        with new_session() as session:
            target = session.get(Device, device_id_value)
            if target is None:
                raise RuntimeError("対象の機器が見つかりません")
            return _draft_read(
                target, config_draft.draft(session, target, request_text, base=base_text)
            )

    return _job_read(jobs.start("config-draft", work))


@router.get("/jobs/{job_id}", response_model=JobRead)
def read_job(job_id: str) -> JobRead:
    """裏の処理の様子。終わっていれば中身も返す。"""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その処理は残っていません")
    return _job_read(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: str) -> JobRead:
    """待つのをやめる。

    モデルの生成そのものは途中で切れないので、結果を捨てるだけ。
    画面を待たせ続けないための操作。
    """
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その処理は残っていません")
    return _job_read(job)
