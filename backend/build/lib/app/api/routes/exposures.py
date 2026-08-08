from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, DeviceExposure, ExposureStatus, Remediation
from app.schemas.exposure import (
    ExposureDecision,
    ExposureRowRead,
    ExposureSummaryRead,
    FixConfigRead,
    SyncResultRead,
)
from app.services import exposure as service
from app.services import remediation_config
from app.services.api_helpers import diff_read

router = APIRouter(tags=["exposures"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("/exposures/sync", response_model=SyncResultRead)
def sync_exposures(db: DbSession) -> SyncResultRead:
    """該当状況を洗い直す。CVE や機器を登録した後に実行する。"""
    return service.sync(db)


@router.get("/exposures", response_model=list[ExposureRowRead])
def list_exposures(
    db: DbSession,
    status_filter: str | None = None,
    device_id: int | None = None,
    unhandled_only: bool = False,
) -> list[ExposureRowRead]:
    return service.listing(
        db, status=status_filter, device_id=device_id, unhandled_only=unhandled_only
    )


@router.get("/exposures/summary", response_model=ExposureSummaryRead)
def exposure_summary(db: DbSession) -> ExposureSummaryRead:
    return service.summary(db)


@router.patch("/exposures/{exposure_id}", response_model=ExposureRowRead)
def decide(exposure_id: int, payload: ExposureDecision, db: DbSession) -> ExposureRowRead:
    """対処状況を決める。判断した時点のバージョンを覚えておく。"""
    row = db.get(DeviceExposure, exposure_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象が見つかりません")

    if payload.status is ExposureStatus.ACCEPTED and not (payload.note or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "リスクを受容する場合は理由を残してください",
        )

    if payload.remediation_id is not None and db.get(Remediation, payload.remediation_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "指定された対処がありません")

    row.status = payload.status
    row.remediation_id = payload.remediation_id
    row.decided_by = payload.decided_by
    row.decided_on = date.today()
    row.note = payload.note
    # ここが肝。判断した時点のバージョンを記録し、変わったら差し戻せるようにする
    row.decided_os_version = row.device.os_version if row.device else None
    row.reopened_on = None
    row.reopen_reason = None

    db.commit()
    db.refresh(row)
    return service._to_row(row, date.today())


# ---------------------------------------------------------------------------
# 対応版 Config
# ---------------------------------------------------------------------------


def _fix_read(fix: remediation_config.FixConfig) -> FixConfigRead:
    return FixConfigRead(
        device_id=fix.device_id,
        hostname=fix.hostname,
        cve_id=fix.cve_id,
        remediation_id=fix.remediation_id,
        remediation_summary=fix.remediation_summary,
        vendor=fix.vendor,
        source=fix.source,
        current_full=fix.current_full,
        fixed_full=fix.fixed_full,
        diff=diff_read(fix.diff),
        already_applied=fix.already_applied,
        notes=fix.notes,
    )


def _build(db: Session, device_id: int, remediation_id: int) -> remediation_config.FixConfig:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    remediation = db.get(Remediation, remediation_id)
    if remediation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の対処が見つかりません")

    try:
        return remediation_config.build_fix(db, device, remediation)
    except remediation_config.FixError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:  # テンプレート展開の失敗など
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get(
    "/devices/{device_id}/remediations/{remediation_id}/config", response_model=FixConfigRead
)
def fix_config(device_id: int, remediation_id: int, db: DbSession) -> FixConfigRead:
    """対処を当てた「対応版 Config」と、その投入コマンドを出す。"""
    return _fix_read(_build(db, device_id, remediation_id))


@router.get("/devices/{device_id}/remediations/{remediation_id}/config/download/{kind}")
def download_fix(device_id: int, remediation_id: int, kind: str, db: DbSession) -> Response:
    fix = _build(db, device_id, remediation_id)
    bodies = {
        "current": (fix.current_full, f"{fix.hostname}-current.txt"),
        "fixed": (fix.fixed_full, f"{fix.hostname}-{fix.cve_id}-fixed.txt"),
        "diff": (fix.diff.script, f"{fix.hostname}-{fix.cve_id}-diff.txt"),
        "rollback": (fix.diff.rollback_script, f"{fix.hostname}-{fix.cve_id}-rollback.txt"),
    }
    if kind not in bodies:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "その種別の出力はありません")
    if kind == "diff" and not fix.diff.verified:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "投入コマンドが対応版 config に収束しないため出力できません",
        )

    body, filename = bodies[kind]
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/exposures/{exposure_id}/verify-config", response_model=ExposureRowRead)
def verify_config(exposure_id: int, db: DbSession) -> ExposureRowRead:
    """対処の設定が現行 config に本当に入っているかを確かめる。

    「やったつもり」を潰すための裏取り。差分が空なら入っている。
    """
    row = db.get(DeviceExposure, exposure_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象が見つかりません")
    if row.remediation_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "確認するには、適用した対処を指定してください",
        )

    fix = _build(db, row.device_id, row.remediation_id)
    row.config_verified = fix.already_applied
    row.config_verified_on = date.today()
    db.commit()
    db.refresh(row)
    return service._to_row(row, date.today())
