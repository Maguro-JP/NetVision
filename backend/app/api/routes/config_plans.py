from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
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
from app.schemas.jobs import JobRead, job_read
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

    def work(_job) -> ConfigDraftRead:  # noqa: ANN001
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

    return job_read(jobs.start("config-draft", work, label=device.hostname))


# ---------------------------------------------------------------------------
# 「ここに機器を足したら」の設計
# ---------------------------------------------------------------------------


class AdditionRequestBody(BaseModel):
    """足す機器の希望。決めていないところは空のままでよい。"""

    hostname: str = Field(description="足す機器の名前")
    vendor: str = Field(default="", description="空なら上位機器と同じ書式で起こす")
    model: str = ""
    purpose: str = Field(default="", description="用途。VLAN の名前と description に使う")
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    subnet: str | None = Field(default=None, description="例 10.10.30.0/24。空なら既存から決める")
    uplink_interface: str | None = Field(default=None, description="上位のどのポートに挿すか")


class DecisionRead(BaseModel):
    topic: str
    choice: str
    why: str
    evidence: list[str]


class AdditionPlanRead(BaseModel):
    upstream_hostname: str
    hostname: str
    vlan_id: int | None
    vlan_name: str
    subnet: str | None
    gateway_ip: str | None
    device_ip: str | None
    upstream_interface: str | None
    device_uplink: str
    routing: str
    decisions: list[DecisionRead]
    warnings: list[str]
    checks: list[str]
    upstream_config: str
    device_config: str
    #: Config そのものには書かない補足。画面で Config の隣に出す
    upstream_notes: list[str]
    device_notes: list[str]


@router.post("/devices/{device_id}/addition", response_model=AdditionPlanRead)
def plan_addition(device_id: int, payload: AdditionRequestBody, db: DbSession) -> AdditionPlanRead:
    """その機器の下に1台足すときの設計を、上位の Config を根拠に起こす。

    VLAN 番号・セグメント・アドレス・使うポートを決め、上位側と新機器側の
    両方の Config を出す。決めた理由と、その根拠にした行も一緒に返す。
    理由が言えない提案は人が確認しようが無いため。

    ここでは保存しないし、実機にも投入しない。
    """
    from app.services import addition

    if not payload.hostname.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "足す機器の名前を書いてください")

    try:
        plan = addition.build(
            db,
            addition.AdditionRequest(
                upstream_device_id=device_id,
                hostname=payload.hostname.strip(),
                vendor=payload.vendor,
                model=payload.model,
                purpose=payload.purpose,
                vlan_id=payload.vlan_id,
                subnet=payload.subnet,
                uplink_interface=payload.uplink_interface,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return AdditionPlanRead(
        upstream_hostname=plan.upstream_hostname,
        hostname=plan.hostname,
        vlan_id=plan.vlan_id,
        vlan_name=plan.vlan_name,
        subnet=plan.subnet,
        gateway_ip=plan.gateway_ip,
        device_ip=plan.device_ip,
        upstream_interface=plan.upstream_interface,
        device_uplink=plan.device_uplink,
        routing=plan.routing,
        decisions=[
            DecisionRead(topic=d.topic, choice=d.choice, why=d.why, evidence=d.evidence)
            for d in plan.decisions
        ],
        warnings=plan.warnings,
        checks=plan.checks,
        upstream_config=plan.upstream_config,
        device_config=plan.device_config,
        upstream_notes=plan.upstream_notes,
        device_notes=plan.device_notes,
    )


# ---------------------------------------------------------------------------
# L3 の中身と、気になるところ
# ---------------------------------------------------------------------------


class FindingRead(BaseModel):
    level: str
    title: str
    why: str
    evidence: list[str]


class L3Read(BaseModel):
    hostname: str
    has_config: bool
    routes: list[dict]
    ospf: dict | None
    bgp: dict | None
    redundancy: list[dict]
    protocols: list[dict]
    policies: list[dict]
    features: list[dict]
    findings: list[FindingRead]


@router.get("/devices/{device_id}/l3", response_model=L3Read)
def read_l3(device_id: int, db: DbSession) -> L3Read:
    """その機器の L3（経路・OSPF・BGP・冗長）と、気になるところ。

    経路の設定は、間違っていても半分は動く。だから気づくのが遅れる。
    Config から読み取れる範囲で、先に挙げておく。
    """
    from app.services import l3review

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")

    report = l3review.review(db, device)
    return L3Read(
        hostname=report.hostname,
        has_config=report.has_config,
        routes=report.routes,
        ospf=report.ospf,
        bgp=report.bgp,
        redundancy=report.redundancy,
        protocols=report.protocols,
        policies=report.policies,
        features=report.features,
        findings=[
            FindingRead(level=f.level, title=f.title, why=f.why, evidence=f.evidence)
            for f in report.findings
        ],
    )


# ---------------------------------------------------------------------------
# ことばで頼む
# ---------------------------------------------------------------------------


class AskBody(BaseModel):
    text: str = Field(
        description="やりたいこと。例:「本社の core-sw-01 の下に floor3-sw-01 を増設したい」"
    )
    #: 読み取りを人が直したとき。指定があればそちらを使う
    upstream_device_id: int | None = None
    new_hostname: str | None = None
    purpose: str | None = None


class ReadingRead(BaseModel):
    site: str | None
    site_id: int | None
    upstream_hostname: str | None
    upstream_device_id: int | None
    new_hostname: str | None
    purpose: str
    notes: list[str]


class AskRead(BaseModel):
    """読み取った内容と、そこから起こした設計。

    読み取りは必ず返す。人が見て違っていれば直せる形にするため。
    """

    reading: ReadingRead
    upstream_choices: list[dict]
    questions: list[str]
    plan: AdditionPlanRead | None


@router.post("/ask/addition", response_model=AskRead)
def ask_addition(payload: AskBody, db: DbSession) -> AskRead:
    """書いた文章から、増設の設計を起こす。

    台帳にある名前が文章に出てきたときだけ当てる。文章から意味を推し量って
    別の機器の設計を出すのが、いちばん困るため。1つに絞れないときは
    候補を返して、設計は起こさない。
    """
    from app.services import addition, askplan

    understanding = askplan.understand(db, payload.text)
    reading = understanding.reading

    # 人が直した内容があれば、そちらを優先する
    if payload.upstream_device_id is not None:
        device = db.get(Device, payload.upstream_device_id)
        if device is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "その機器は台帳にありません")
        reading.upstream_device_id = device.id
        reading.upstream_hostname = device.hostname
        understanding.upstream_choices = []
        understanding.questions = [q for q in understanding.questions if "どの機器の下" not in q]
    if payload.new_hostname:
        reading.new_hostname = payload.new_hostname.strip()
        understanding.questions = [q for q in understanding.questions if "足す機器の名前" not in q]
    if payload.purpose is not None:
        reading.purpose = payload.purpose.strip()

    read = ReadingRead(
        site=reading.site,
        site_id=reading.site_id,
        upstream_hostname=reading.upstream_hostname,
        upstream_device_id=reading.upstream_device_id,
        new_hostname=reading.new_hostname,
        purpose=reading.purpose,
        notes=reading.notes,
    )

    if reading.upstream_device_id is None or not reading.new_hostname:
        return AskRead(
            reading=read,
            upstream_choices=understanding.upstream_choices,
            questions=understanding.questions,
            plan=None,
        )

    plan = addition.build(
        db,
        addition.AdditionRequest(
            upstream_device_id=reading.upstream_device_id,
            hostname=reading.new_hostname,
            purpose=reading.purpose,
        ),
    )
    return AskRead(
        reading=read,
        upstream_choices=[],
        questions=understanding.questions,
        plan=AdditionPlanRead(
            upstream_hostname=plan.upstream_hostname,
            hostname=plan.hostname,
            vlan_id=plan.vlan_id,
            vlan_name=plan.vlan_name,
            subnet=plan.subnet,
            gateway_ip=plan.gateway_ip,
            device_ip=plan.device_ip,
            upstream_interface=plan.upstream_interface,
            device_uplink=plan.device_uplink,
            routing=plan.routing,
            decisions=[
                DecisionRead(topic=d.topic, choice=d.choice, why=d.why, evidence=d.evidence)
                for d in plan.decisions
            ],
            warnings=plan.warnings,
            checks=plan.checks,
            upstream_config=plan.upstream_config,
            device_config=plan.device_config,
            upstream_notes=plan.upstream_notes,
            device_notes=plan.device_notes,
        ),
    )
