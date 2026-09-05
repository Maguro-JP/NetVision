import csv
import io
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Device, DeviceStatus
from app.models.reference import ProductReference, ReferenceSource, Vulnerability
from app.schemas.reference import (
    DeviceVulnerabilityRead,
    ProductImportResultRead,
    ProductReferenceCreate,
    ProductReferenceRead,
    ProductReferenceUpdate,
    ReferenceFreshness,
    VulnerabilityDetail,
    VulnerabilityImportResultRead,
    VulnerabilityRead,
    VulnerabilitySummary,
)
from app.services import appsettings, versions
from app.services import reference as service
from app.services.device_query import DeviceFilters, query_devices
from app.services.tablefile import TableFileError, read_table

router = APIRouter(prefix="/reference", tags=["reference"])

DbSession = Annotated[Session, Depends(get_db)]

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _stale_days() -> int:
    """リファレンスが古いと見なす日数。画面で決めた値を優先する。"""
    return appsettings.number("reference_stale_days", settings.reference_stale_days)


def _device_counts(db: Session) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for vendor, model in db.execute(select(Device.vendor, Device.model)).all():
        key = (versions.normalize_name(vendor), versions.normalize_name(model))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _to_product_read(
    product: ProductReference, counts: dict[tuple[str, str], int], today: date
) -> ProductReferenceRead:
    age = (today - product.fetched_at).days if product.fetched_at else None
    return ProductReferenceRead(
        id=product.id,
        vendor=product.vendor,
        model=product.model,
        product_name=product.product_name,
        eos_date=product.eos_date,
        eol_date=product.eol_date,
        end_of_sw_date=product.end_of_sw_date,
        last_support_date=product.last_support_date,
        recommended_replacement=product.recommended_replacement,
        source=product.source,
        source_url=product.source_url,
        fetched_at=product.fetched_at,
        notes=product.notes,
        device_count=counts.get(
            (versions.normalize_name(product.vendor), versions.normalize_name(product.model)), 0
        ),
        age_days=age,
        stale=age is None or age > _stale_days(),
    )


# ---------------------------------------------------------------------------
# 製品リファレンス
# ---------------------------------------------------------------------------


@router.get("/products", response_model=list[ProductReferenceRead])
def list_products(
    db: DbSession,
    q: str | None = Query(default=None, description="ベンダー / 型番 / 製品名の部分一致"),
    stale_only: bool = Query(default=False, description="情報が古いものだけ"),
) -> list[ProductReferenceRead]:
    today = date.today()
    counts = _device_counts(db)

    stmt = select(ProductReference).order_by(ProductReference.vendor, ProductReference.model)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            ProductReference.vendor.ilike(like)
            | ProductReference.model.ilike(like)
            | ProductReference.product_name.ilike(like)
        )

    products = [_to_product_read(p, counts, today) for p in db.scalars(stmt)]
    return [p for p in products if p.stale] if stale_only else products


@router.post("/products", response_model=ProductReferenceRead, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductReferenceCreate, db: DbSession) -> ProductReferenceRead:
    duplicate = db.scalar(
        select(ProductReference).where(
            func.lower(ProductReference.vendor) == payload.vendor.lower(),
            func.lower(ProductReference.model) == payload.model.lower(),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{payload.vendor} {payload.model} は登録済みです"
        )

    product = ProductReference(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return _to_product_read(product, _device_counts(db), date.today())


def _load_product(db: Session, product_id: int) -> ProductReference:
    product = db.get(ProductReference, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"product {product_id} が見つかりません")
    return product


@router.patch("/products/{product_id}", response_model=ProductReferenceRead)
def update_product(
    product_id: int, payload: ProductReferenceUpdate, db: DbSession
) -> ProductReferenceRead:
    product = _load_product(db, product_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return _to_product_read(product, _device_counts(db), date.today())


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DbSession) -> None:
    db.delete(_load_product(db, product_id))
    db.commit()


@router.post("/products/import", response_model=ProductImportResultRead)
async def import_products(
    db: DbSession,
    file: UploadFile = File(description="製品リファレンスの CSV / Excel"),
    fetched_at: date | None = Form(
        default=None, description="この情報をベンダー資料から取得した日"
    ),
) -> ProductImportResultRead:
    """ベンダー資料から転記した EoL 情報を取り込む。

    閉域環境ではオンライン取得ができないため、この経路が主になる。
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルが大きすぎます")

    try:
        table = read_table(file.filename or "products.csv", content)
    except TableFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    result = service.import_products(
        db, table, source=ReferenceSource.CSV, fetched_at=fetched_at or date.today()
    )
    return ProductImportResultRead.model_validate(result, from_attributes=True)


@router.get("/products/template.csv")
def product_template() -> StreamingResponse:
    """記入用テンプレート。ベンダー資料からの転記作業に使う。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(service.PRODUCT_FIELD_LABELS.values()))
    writer.writerow(
        [
            "Cisco",
            "C9500-40X",
            "Catalyst 9500 40x10G",
            "2027/01/31",
            "2030/01/31",
            "2028/01/31",
            "2030/01/31",
            "C9500-48Y4C",
            "https://www.cisco.com/...eol.html",
            date.today().strftime("%Y/%m/%d"),
            "ベンダー公式EoL通知より転記",
        ]
    )
    data = ("﻿" + buffer.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="netvision_products_template.csv"'},
    )


# ---------------------------------------------------------------------------
# 脆弱性
# ---------------------------------------------------------------------------


@router.post("/vulnerabilities/import", response_model=VulnerabilityImportResultRead)
async def import_vulnerabilities(
    db: DbSession,
    file: UploadFile = File(description="NVD 2.0 / CISA KEV / 簡易形式の JSON"),
    fetched_at: date | None = Form(default=None),
) -> VulnerabilityImportResultRead:
    """脆弱性データを JSON で取り込む。形式は中身から自動判別する。"""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ファイルが大きすぎます")

    try:
        result = service.import_vulnerabilities(db, content, fetched_at=fetched_at)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return VulnerabilityImportResultRead.model_validate(result, from_attributes=True)


@router.get("/vulnerabilities", response_model=list[VulnerabilityRead])
def list_vulnerabilities(
    db: DbSession,
    q: str | None = None,
    known_exploited: bool | None = None,
    affecting_only: bool = Query(default=False, description="自社機器に該当するものだけ"),
) -> list[VulnerabilityRead]:
    stmt = select(Vulnerability).options(selectinload(Vulnerability.affects))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Vulnerability.cve_id.ilike(like) | Vulnerability.title.ilike(like))
    if known_exploited is not None:
        stmt = stmt.where(Vulnerability.known_exploited == known_exploited)

    vulnerabilities = list(
        db.scalars(
            stmt.order_by(
                Vulnerability.known_exploited.desc(),
                Vulnerability.cvss_score.desc().nulls_last(),
                Vulnerability.cve_id,
            )
        )
    )

    devices = _active_devices(db)
    counts: dict[str, int] = {}
    for device in devices:
        for finding in service.match_device(device, vulnerabilities):
            counts[finding.cve_id] = counts.get(finding.cve_id, 0) + 1

    results = []
    for vulnerability in vulnerabilities:
        count = counts.get(vulnerability.cve_id, 0)
        if affecting_only and count == 0:
            continue
        read = VulnerabilityRead.model_validate(vulnerability, from_attributes=True)
        read.affected_device_count = count
        results.append(read)
    return results


@router.get("/vulnerabilities/{vulnerability_id}", response_model=VulnerabilityDetail)
def get_vulnerability(vulnerability_id: int, db: DbSession) -> VulnerabilityDetail:
    found = db.get(Vulnerability, vulnerability_id, options=[selectinload(Vulnerability.affects)])
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"vulnerability {vulnerability_id} が見つかりません"
        )
    detail = VulnerabilityDetail.model_validate(found, from_attributes=True)
    detail.affected_device_count = sum(
        1 for d in _active_devices(db) if service.match_device(d, [found])
    )
    return detail


@router.delete("/vulnerabilities/{vulnerability_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vulnerability(vulnerability_id: int, db: DbSession) -> None:
    found = db.get(Vulnerability, vulnerability_id)
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"vulnerability {vulnerability_id} が見つかりません"
        )
    db.delete(found)
    db.commit()


def _active_devices(db: Session) -> list[Device]:
    return [d for d in query_devices(db, DeviceFilters()) if d.status is not DeviceStatus.RETIRED]


@router.get("/vulnerabilities/matches/summary", response_model=VulnerabilitySummary)
def vulnerability_matches(
    db: DbSession,
    device_id: int | None = Query(default=None, description="特定の機器に絞る"),
) -> VulnerabilitySummary:
    """機器と脆弱性を突き合わせた結果。何で一致したかも返す。"""
    vulnerabilities = service.load_vulnerabilities(db)
    devices = _active_devices(db)
    if device_id is not None:
        devices = [d for d in devices if d.id == device_id]

    findings = []
    affected: set[int] = set()
    for device in devices:
        matches = service.match_device(device, vulnerabilities)
        # 判定できていないものを「影響を受ける機器」に数えない。当たると
        # 分かっている台数と混ざると、どちらの数字も意味が薄くなる
        if any(not m.undetermined for m in matches):
            affected.add(device.id)
        findings.extend(matches)

    # 判定できていないものは後ろへ。見落とされないよう、件数は別に出す
    findings.sort(
        key=lambda f: (f.undetermined, not f.known_exploited, -(f.cvss_score or 0), f.hostname)
    )

    return VulnerabilitySummary(
        total_vulnerabilities=len(vulnerabilities),
        devices_affected=len(affected),
        known_exploited_findings=sum(
            1 for f in findings if f.known_exploited and not f.undetermined
        ),
        critical_findings=sum(
            1 for f in findings if (f.cvss_score or 0) >= 9.0 and not f.undetermined
        ),
        high_findings=sum(
            1 for f in findings if 7.0 <= (f.cvss_score or 0) < 9.0 and not f.undetermined
        ),
        undetermined_findings=sum(1 for f in findings if f.undetermined),
        # バージョン未登録の機器は判定できない。件数を出さないと見落とす。
        devices_without_version=sum(1 for d in devices if not d.os_version),
        findings=[DeviceVulnerabilityRead(**f.__dict__) for f in findings],
    )


# ---------------------------------------------------------------------------
# 鮮度
# ---------------------------------------------------------------------------


@router.get("/freshness", response_model=ReferenceFreshness)
def freshness(db: DbSession) -> ReferenceFreshness:
    """リファレンスがどれだけ古いかを返す。

    閉域運用では自動更新されないため、「いつの情報で判断しているか」を
    運用者が意識できるようにしておく必要がある。
    """
    today = date.today()
    threshold = today - timedelta(days=_stale_days())

    products = list(db.scalars(select(ProductReference)))
    vulnerabilities = list(db.scalars(select(Vulnerability)))
    devices = _active_devices(db)

    product_keys = {
        (versions.normalize_name(p.vendor), versions.normalize_name(p.model)) for p in products
    }
    covered = [
        d
        for d in devices
        if (versions.normalize_name(d.vendor), versions.normalize_name(d.model)) in product_keys
    ]
    uncovered = sorted(
        {
            f"{d.vendor} {d.model}"
            for d in devices
            if (versions.normalize_name(d.vendor), versions.normalize_name(d.model))
            not in product_keys
        }
    )

    product_dates = [p.fetched_at for p in products if p.fetched_at]
    vulnerability_dates = [v.fetched_at for v in vulnerabilities if v.fetched_at]

    return ReferenceFreshness(
        product_count=len(products),
        products_with_dates=sum(1 for p in products if p.eol_date or p.eos_date),
        products_stale=sum(1 for p in products if p.fetched_at is None or p.fetched_at < threshold),
        products_never_fetched=sum(1 for p in products if p.fetched_at is None),
        oldest_product_fetch=min(product_dates) if product_dates else None,
        vulnerability_count=len(vulnerabilities),
        vulnerabilities_stale=sum(
            1 for v in vulnerabilities if v.fetched_at is None or v.fetched_at < threshold
        ),
        oldest_vulnerability_fetch=min(vulnerability_dates) if vulnerability_dates else None,
        stale_after_days=_stale_days(),
        devices_total=len(devices),
        devices_with_product_reference=len(covered),
        devices_without_reference=uncovered,
    )
