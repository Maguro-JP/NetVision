from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.replacement import ImpactRead, ReplacementPlanRead
from app.services import replacement as service

router = APIRouter(tags=["replacement"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/replacement/plan", response_model=ReplacementPlanRead)
def replacement_plan(db: DbSession, years: int = Query(default=5, ge=1, le=15)):
    """年度別の更改計画。稟議で要るのは「いつ・何台・いくら」なのでその形で返す。"""
    return service.build_plan(db, years=years)


@router.get("/replacement/impact/{device_id}", response_model=ImpactRead)
def replacement_impact(device_id: int, db: DbSession):
    """その機器を止めたときに経路が失われる機器。"""
    try:
        return service.impact_of(db, device_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
