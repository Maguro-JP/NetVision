from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Remediation, Vulnerability
from app.schemas.remediation import (
    DeviceRemediationRead,
    RemediationCreate,
    RemediationRead,
    RemediationUpdate,
)
from app.services import remediation as service

router = APIRouter(tags=["remediation"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_read(remediation: Remediation) -> RemediationRead:
    read = RemediationRead.model_validate(remediation, from_attributes=True)
    read.cve_id = remediation.vulnerability.cve_id if remediation.vulnerability else None
    read.source_document_title = (
        remediation.source_document.title if remediation.source_document else None
    )
    read.config_template_name = (
        remediation.config_template.name if remediation.config_template else None
    )
    return read


@router.get("/remediations", response_model=list[RemediationRead])
def list_remediations(db: DbSession, cve_id: str | None = None) -> list[RemediationRead]:
    stmt = select(Remediation).order_by(Remediation.id.desc())
    if cve_id:
        stmt = stmt.join(Vulnerability).where(Vulnerability.cve_id == cve_id)
    return [_to_read(r) for r in db.scalars(stmt)]


@router.post("/remediations", response_model=RemediationRead, status_code=status.HTTP_201_CREATED)
def create_remediation(payload: RemediationCreate, db: DbSession) -> RemediationRead:
    vulnerability = db.scalars(
        select(Vulnerability).where(Vulnerability.cve_id == payload.cve_id)
    ).first()
    if vulnerability is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{payload.cve_id} が登録されていません。先に脆弱性情報を取り込んでください",
        )

    data = payload.model_dump(exclude={"cve_id"})
    remediation = Remediation(vulnerability_id=vulnerability.id, **data)
    db.add(remediation)
    db.commit()
    db.refresh(remediation)
    return _to_read(remediation)


@router.patch("/remediations/{remediation_id}", response_model=RemediationRead)
def update_remediation(
    remediation_id: int, payload: RemediationUpdate, db: DbSession
) -> RemediationRead:
    remediation = db.get(Remediation, remediation_id)
    if remediation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の対処が見つかりません")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(remediation, field, value)
    db.commit()
    db.refresh(remediation)
    return _to_read(remediation)


@router.delete("/remediations/{remediation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remediation(remediation_id: int, db: DbSession) -> Response:
    remediation = db.get(Remediation, remediation_id)
    if remediation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の対処が見つかりません")
    db.delete(remediation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/devices/{device_id}/remediation", response_model=DeviceRemediationRead)
def device_remediation(device_id: int, db: DbSession) -> DeviceRemediationRead:
    """その機器の該当CVEと、それぞれの対処をまとめて返す。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    return service.for_device(db, device)
