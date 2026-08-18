import io
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, DeviceStatus, DeviceType, Site
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
    *,
    hostname: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    os_version: str | None = None,
    mgmt_ip: str | None = None,
    rack: str | None = None,
    notes: str | None = None,
    product: str | None = None,
    site_name: str | None = None,
    maintenance: str | None = None,
    eol: str | None = None,
    incomplete: bool = False,
) -> DeviceFilters:
    return DeviceFilters(
        q=q,
        hostname=hostname,
        vendor=vendor,
        model=model,
        serial_number=serial_number,
        os_version=os_version,
        mgmt_ip=mgmt_ip,
        rack=rack,
        notes=notes,
        product=product,
        site_name=site_name,
        maintenance=maintenance,
        eol=eol,
        device_type=device_type,
        status=device_status,
        site_id=site_id,
        severity=severity,
        incomplete=incomplete,
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
    hostname: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    os_version: str | None = None,
    mgmt_ip: str | None = None,
    rack: str | None = None,
    notes: str | None = None,
    product: str | None = None,
    site_name: str | None = None,
    maintenance: str | None = None,
    eol: str | None = None,
    device_type: DeviceType | None = None,
    device_status: DeviceStatus | None = Query(default=None, alias="status"),
    site_id: int | None = None,
    severity: Severity | None = None,
    incomplete: bool = Query(default=False),
    include_status: bool = Query(default=True, description="保守期限・残日数などの算出列を含める"),
    encoding: str = Query(default="utf-8", description="utf-8（BOM付き） または shift_jis"),
) -> StreamingResponse:
    """機器台帳を CSV で書き出す。列見出しは取り込み側と同じなので、そのまま取り込み直せる。"""
    if encoding not in CSV_ENCODINGS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "encoding は utf-8 または shift_jis を指定してください",
        )

    devices = query_devices(
        db,
        _filters(
            q,
            vendor,
            device_type,
            device_status,
            site_id,
            severity,
            hostname=hostname,
            model=model,
            serial_number=serial_number,
            os_version=os_version,
            mgmt_ip=mgmt_ip,
            rack=rack,
            notes=notes,
            product=product,
            site_name=site_name,
            maintenance=maintenance,
            eol=eol,
            incomplete=incomplete,
        ),
    )
    data = exporter.to_csv(devices, include_status=include_status, encoding=CSV_ENCODINGS[encoding])
    return StreamingResponse(io.BytesIO(data), media_type="text/csv", headers=_disposition("csv"))


@router.get("/devices.xlsx")
def export_devices_xlsx(
    db: DbSession,
    q: str | None = None,
    hostname: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    os_version: str | None = None,
    mgmt_ip: str | None = None,
    rack: str | None = None,
    notes: str | None = None,
    product: str | None = None,
    site_name: str | None = None,
    maintenance: str | None = None,
    eol: str | None = None,
    device_type: DeviceType | None = None,
    device_status: DeviceStatus | None = Query(default=None, alias="status"),
    site_id: int | None = None,
    severity: Severity | None = None,
    incomplete: bool = Query(default=False),
    include_status: bool = True,
) -> StreamingResponse:
    """機器台帳を Excel で書き出す。日付は日付セル、金額は数値セルで入る。"""
    devices = query_devices(
        db,
        _filters(
            q,
            vendor,
            device_type,
            device_status,
            site_id,
            severity,
            hostname=hostname,
            model=model,
            serial_number=serial_number,
            os_version=os_version,
            mgmt_ip=mgmt_ip,
            rack=rack,
            notes=notes,
            product=product,
            site_name=site_name,
            maintenance=maintenance,
            eol=eol,
            incomplete=incomplete,
        ),
    )
    data = exporter.to_xlsx(devices, include_status=include_status)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_disposition("xlsx"),
    )


def _sites_disposition(extension: str) -> dict[str, str]:
    filename = f"netvision_sites_{date.today():%Y%m%d}.{extension}"
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    }


def _site_rows(db: Session) -> list[tuple[Site, int]]:
    """拠点と、その拠点に置いてある機器の台数。

    台数まで出すのは、閉鎖した拠点に機器が残っていないかを、書き出した表の
    上で数えられるようにするため。
    """
    counts = dict(
        db.execute(select(Device.site_id, func.count(Device.id)).group_by(Device.site_id)).all()
    )
    sites = list(db.scalars(select(Site)))
    sites.sort(key=lambda s: (s.code is None, (s.code or "").casefold(), s.name.casefold()))
    return [(site, counts.get(site.id, 0)) for site in sites]


@router.get("/sites.csv")
def export_sites_csv(
    db: DbSession,
    encoding: str = Query(default="utf-8", pattern="^(utf-8|shift_jis)$"),
) -> StreamingResponse:
    """拠点台帳を CSV で書き出す。直してそのまま取り込み直せる並び。"""
    data = exporter.sites_to_csv(_site_rows(db), encoding=CSV_ENCODINGS[encoding])
    return StreamingResponse(
        io.BytesIO(data), media_type="text/csv", headers=_sites_disposition("csv")
    )


@router.get("/sites.xlsx")
def export_sites_xlsx(db: DbSession) -> StreamingResponse:
    """拠点台帳を Excel で書き出す。日付は日付セルで入る。"""
    data = exporter.sites_to_xlsx(_site_rows(db))
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_sites_disposition("xlsx"),
    )
