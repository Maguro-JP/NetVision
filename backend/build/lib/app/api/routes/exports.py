import io
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeviceStatus, DeviceType
from app.services import exporter
from app.services.device_query import DeviceFilters, query_devices
from app.services.lifecycle import Severity

router = APIRouter(prefix="/export", tags=["export"])

DbSession = Annotated[Session, Depends(get_db)]

# Excel で開く前提の CSV は、環境に合わせて文字コードを選べるようにする
CSV_ENCODINGS = {
    "utf-8": "utf-8-sig",  # BOM 付き。Excel でも文字化けしない
    "shift_jis": "cp932",  # 古い Excel / 既存ツールに渡す場合
}


def _filters(
    q: str | None,
    vendor: str | None,
    device_type: DeviceType | None,
    device_status: DeviceStatus | None,
    site_id: int | None,
    severity: Severity | None,
) -> DeviceFilters:
    return DeviceFilters(
        q=q,
        vendor=vendor,
        device_type=device_type,
        status=device_status,
        site_id=site_id,
        severity=severity,
    )


def _disposition(extension: str) -> dict[str, str]:
    filename = f"netvision_devices_{date.today():%Y%m%d}.{extension}"
    # 日本語ファイル名は使っていないが、将来に備えて RFC 5987 形式も併記する
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    }


@router.get("/devices.csv")
def export_devices_csv(
    db: DbSession,
    q: str | None = None,
    vendor: str | None = None,
    device_type: DeviceType | None = None,
    device_status: DeviceStatus | None = Query(default=None, alias="status"),
    site_id: int | None = None,
    severity: Severity | None = None,
    include_status: bool = Query(default=True, description="保守期限・残日数などの算出列を含める"),
    encoding: str = Query(default="utf-8", description="utf-8（BOM付き） または shift_jis"),
) -> StreamingResponse:
    """機器台帳を CSV で書き出す。列見出しは取り込み側と同じなので、そのまま取り込み直せる。"""
    if encoding not in CSV_ENCODINGS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "encoding は utf-8 または shift_jis を指定してください",
        )

    devices = query_devices(db, _filters(q, vendor, device_type, device_status, site_id, severity))
    data = exporter.to_csv(devices, include_status=include_status, encoding=CSV_ENCODINGS[encoding])
    return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers=_disposition("csv"))


@router.get("/devices.xlsx")
def export_devices_xlsx(
    db: DbSession,
    q: str | None = None,
    vendor: str | None = None,
    device_type: DeviceType | None = None,
    device_status: DeviceStatus | None = Query(default=None, alias="status"),
    site_id: int | None = None,
    severity: Severity | None = None,
    include_status: bool = True,
) -> StreamingResponse:
    """機器台帳を Excel で書き出す。日付は日付セル、金額は数値セルで入る。"""
    devices = query_devices(db, _filters(q, vendor, device_type, device_status, site_id, severity))
    data = exporter.to_xlsx(devices, include_status=include_status)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_disposition("xlsx"),
    )
