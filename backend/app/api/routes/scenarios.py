from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Device
from app.models.scenario import Scenario, ScenarioAction, ScenarioItem
from app.schemas.scenarios import (
    ScenarioCreate,
    ScenarioDetail,
    ScenarioItemCreate,
    ScenarioItemRead,
    ScenarioItemUpdate,
    ScenarioRead,
    ScenarioSummaryRead,
    ScenarioUpdate,
)
from app.services import scenarios as service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

DbSession = Annotated[Session, Depends(get_db)]


def _load(db: Session, scenario_id: int) -> Scenario:
    scenario = db.get(Scenario, scenario_id, options=[selectinload(Scenario.items)])
    if scenario is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"scenario {scenario_id} が見つかりません")
    return scenario


def _to_detail(scenario: Scenario) -> ScenarioDetail:
    return ScenarioDetail(
        id=scenario.id,
        name=scenario.name,
        description=scenario.description,
        status=scenario.status,
        target_date=scenario.target_date,
        item_count=len(scenario.items),
        items=[ScenarioItemRead.model_validate(item) for item in scenario.items],
    )


@router.get("", response_model=list[ScenarioRead])
def list_scenarios(db: DbSession) -> list[ScenarioRead]:
    counts = dict(
        db.execute(
            select(ScenarioItem.scenario_id, func.count(ScenarioItem.id)).group_by(
                ScenarioItem.scenario_id
            )
        ).all()
    )
    return [
        ScenarioRead(
            id=s.id,
            name=s.name,
            description=s.description,
            status=s.status,
            target_date=s.target_date,
            item_count=counts.get(s.id, 0),
        )
        for s in db.scalars(select(Scenario).order_by(Scenario.id.desc()))
    ]


@router.post("", response_model=ScenarioDetail, status_code=status.HTTP_201_CREATED)
def create_scenario(payload: ScenarioCreate, db: DbSession) -> ScenarioDetail:
    if db.scalar(select(Scenario).where(Scenario.name == payload.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"「{payload.name}」は登録済みです")
    scenario = Scenario(**payload.model_dump())
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return _to_detail(scenario)


@router.get("/{scenario_id}", response_model=ScenarioDetail)
def get_scenario(scenario_id: int, db: DbSession) -> ScenarioDetail:
    return _to_detail(_load(db, scenario_id))


@router.patch("/{scenario_id}", response_model=ScenarioDetail)
def update_scenario(scenario_id: int, payload: ScenarioUpdate, db: DbSession) -> ScenarioDetail:
    scenario = _load(db, scenario_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(scenario, key, value)
    db.commit()
    db.refresh(scenario)
    return _to_detail(scenario)


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: int, db: DbSession) -> None:
    db.delete(_load(db, scenario_id))
    db.commit()


@router.get("/{scenario_id}/summary", response_model=ScenarioSummaryRead)
def scenario_summary(scenario_id: int, db: DbSession) -> ScenarioSummaryRead:
    """現状の構成と、案を適用した構成を同じ指標で比較する。"""
    summary = service.summarize(db, _load(db, scenario_id))
    return ScenarioSummaryRead.model_validate(summary, from_attributes=True)


@router.post("/{scenario_id}/generate", response_model=ScenarioDetail)
def generate_scenario_items(
    scenario_id: int,
    db: DbSession,
    within_days: int = Query(
        default=730, ge=0, le=3650, description="この日数以内に期限を迎える機器"
    ),
) -> ScenarioDetail:
    """期限が近い機器から入れ替え案のたたき台を作る。既に案がある機器は重複させない。"""
    scenario = _load(db, scenario_id)
    service.generate_items(db, scenario, within_days=within_days)
    db.commit()
    db.refresh(scenario)
    return _to_detail(scenario)


def _validate_item(db: Session, payload: ScenarioItemCreate | ScenarioItemUpdate) -> Device | None:
    device = None
    if payload.device_id is not None:
        device = db.get(Device, payload.device_id)
        if device is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"device {payload.device_id} が存在しません"
            )
    if (
        payload.action in (ScenarioAction.REPLACE, ScenarioAction.REMOVE)
        and payload.device_id is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "入れ替え・撤去には対象の機器を指定してください",
        )
    return device


@router.post(
    "/{scenario_id}/items", response_model=ScenarioItemRead, status_code=status.HTTP_201_CREATED
)
def add_item(scenario_id: int, payload: ScenarioItemCreate, db: DbSession) -> ScenarioItem:
    _load(db, scenario_id)
    device = _validate_item(db, payload)

    item = ScenarioItem(
        scenario_id=scenario_id,
        device_label=device.hostname if device else None,
        **payload.model_dump(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _load_item(db: Session, item_id: int) -> ScenarioItem:
    item = db.get(ScenarioItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} が見つかりません")
    return item


@router.patch("/items/{item_id}", response_model=ScenarioItemRead)
def update_item(item_id: int, payload: ScenarioItemUpdate, db: DbSession) -> ScenarioItem:
    item = _load_item(db, item_id)
    changes = payload.model_dump(exclude_unset=True)

    if "device_id" in changes and changes["device_id"] is not None:
        device = db.get(Device, changes["device_id"])
        if device is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"device {changes['device_id']} が存在しません",
            )
        item.device_label = device.hostname

    for key, value in changes.items():
        setattr(item, key, value)

    if item.action in (ScenarioAction.REPLACE, ScenarioAction.REMOVE) and item.device_id is None:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "入れ替え・撤去には対象の機器を指定してください",
        )

    db.commit()
    db.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession) -> None:
    db.delete(_load_item(db, item_id))
    db.commit()
