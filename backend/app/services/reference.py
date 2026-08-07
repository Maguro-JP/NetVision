"""リファレンスデータ（型番のライフサイクルと脆弱性）の取り込みと突合。

閉域環境での運用を前提にしているため、**オフラインでの持ち込みを第一級**に扱う。
NVD や CISA KEV から落とした JSON をそのまま食えるようにし、
オンライン取得ができない現場でも同じ機能が使えるようにしている。

どのデータにも「出典」と「取得日」を必ず持たせる。
リファレンスは古くなるものなので、いつ時点の情報かが分からないと判断に使えない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Device
from app.models.reference import (
    ProductReference,
    ReferenceSource,
    Vulnerability,
    VulnerabilityAffect,
)
from app.services import versions
from app.services.tablefile import Table

# 製品リファレンス CSV の列名（取り込み側と同じく別名で吸収する）
PRODUCT_FIELD_LABELS = {
    "vendor": "ベンダー",
    "model": "型番",
    "product_name": "製品名",
    "eos_date": "販売終了(EoS)",
    "eol_date": "サポート終了(EoL)",
    "end_of_sw_date": "ソフトウェア保守終了",
    "last_support_date": "最終サポート日",
    "recommended_replacement": "推奨後継機",
    "source_url": "出典URL",
    "fetched_at": "情報取得日",
    "notes": "備考",
}

PRODUCT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "vendor": ("vendor", "maker", "manufacturer", "ベンダー", "ベンダ", "メーカー", "メーカ"),
    "model": ("model", "pid", "productid", "型番", "型式", "機種", "製品型番"),
    "product_name": ("productname", "name", "製品名", "名称"),
    "eos_date": ("eos", "eosdate", "endofsale", "販売終了", "販売終了日"),
    "eol_date": (
        "eol",
        "eoldate",
        "endofsupport",
        "endoflife",
        "lastdateofsupport",
        "サポート終了",
        "サポート終了日",
        "保守終了日",
    ),
    "end_of_sw_date": (
        "endofswmaintenance",
        "endofsoftware",
        "swmaintenance",
        "ソフトウェア保守終了",
        "ソフト保守終了",
    ),
    "last_support_date": ("lastsupport", "lastdayofsupport", "最終サポート日", "最終保守日"),
    "recommended_replacement": (
        "replacement",
        "successor",
        "migration",
        "推奨後継機",
        "後継機",
        "後継製品",
    ),
    "source_url": ("url", "sourceurl", "reference", "出典", "出典url", "参照url"),
    "fetched_at": ("fetchedat", "retrieved", "asof", "情報取得日", "取得日", "確認日"),
    "notes": ("notes", "note", "remarks", "備考", "メモ"),
}


@dataclass
class ProductImportRow:
    row_number: int
    vendor: str | None
    model: str | None
    action: str  # created / updated / unchanged / error
    message: str | None = None


@dataclass
class ProductImportResult:
    total_rows: int
    created: int
    updated: int
    unchanged: int
    errors: int
    mapping: dict[str, str]
    unmapped_columns: list[str]
    missing_required_fields: list[str]
    rows: list[ProductImportRow] = field(default_factory=list)


def _build_alias_index() -> dict[str, str]:
    from app.services.importer import normalize_header

    index: dict[str, str] = {}
    for name, label in PRODUCT_FIELD_LABELS.items():
        index[normalize_header(label)] = name
    for name, aliases in PRODUCT_FIELD_ALIASES.items():
        index.setdefault(normalize_header(name), name)
        for alias in aliases:
            index.setdefault(normalize_header(alias), name)
    return index


_PRODUCT_ALIAS_INDEX = _build_alias_index()


def detect_product_mapping(headers: list[str]) -> dict[str, str]:
    from app.services.importer import normalize_header

    mapping: dict[str, str] = {}
    used: set[str] = set()
    for header in headers:
        target = _PRODUCT_ALIAS_INDEX.get(normalize_header(header))
        if target and target not in used:
            mapping[header] = target
            used.add(target)
    return mapping


def import_products(
    db: Session,
    table: Table,
    *,
    source: ReferenceSource = ReferenceSource.CSV,
    fetched_at: date | None = None,
    commit: bool = True,
) -> ProductImportResult:
    """製品リファレンスの CSV / Excel を取り込む。ベンダー資料からの転記を想定。"""
    from app.services.importer import CellValueError, parse_date

    mapping = detect_product_mapping(table.headers)
    mapped = set(mapping.values())
    missing = [f for f in ("vendor", "model") if f not in mapped]
    unmapped = [h for h in table.headers if h not in mapping]

    if missing:
        return ProductImportResult(
            total_rows=len(table.rows),
            created=0,
            updated=0,
            unchanged=0,
            errors=0,
            mapping=mapping,
            unmapped_columns=unmapped,
            missing_required_fields=missing,
            rows=[],
        )

    existing = {(_key(p.vendor), _key(p.model)): p for p in db.scalars(select(ProductReference))}
    date_fields = {"eos_date", "eol_date", "end_of_sw_date", "last_support_date", "fetched_at"}

    results: list[ProductImportRow] = []
    created = updated = unchanged = errors = 0

    for index, raw in enumerate(table.rows, start=2):
        values: dict[str, object] = {}
        issues: list[str] = []

        for column, target in mapping.items():
            cell = raw.get(column, "")
            try:
                if target in date_fields:
                    values[target] = parse_date(cell)
                else:
                    text = str(cell).strip() if cell is not None else ""
                    values[target] = text or None
            except CellValueError as exc:
                issues.append(str(exc))

        vendor = str(values.get("vendor") or "").strip()
        model = str(values.get("model") or "").strip()
        if not vendor or not model:
            issues.append("ベンダーと型番は必須です")

        if issues:
            errors += 1
            results.append(
                ProductImportRow(
                    row_number=index,
                    vendor=vendor or None,
                    model=model or None,
                    action="error",
                    message=" / ".join(issues),
                )
            )
            continue

        product = existing.get((_key(vendor), _key(model)))
        is_new = product is None
        if product is None:
            product = ProductReference(vendor=vendor, model=model)
            db.add(product)
            existing[(_key(vendor), _key(model))] = product

        before = _snapshot(product)
        for name in PRODUCT_FIELD_LABELS:
            if name in ("vendor", "model"):
                continue
            value = values.get(name)
            if value is not None:
                setattr(product, name, value)

        product.source = source
        if fetched_at is not None and values.get("fetched_at") is None:
            product.fetched_at = fetched_at

        if is_new:
            created += 1
            action = "created"
        elif _snapshot(product) != before:
            updated += 1
            action = "updated"
        else:
            unchanged += 1
            action = "unchanged"

        results.append(
            ProductImportRow(row_number=index, vendor=vendor, model=model, action=action)
        )

    if commit and errors == 0:
        db.commit()
    else:
        db.rollback()

    return ProductImportResult(
        total_rows=len(table.rows),
        created=created,
        updated=updated,
        unchanged=unchanged,
        errors=errors,
        mapping=mapping,
        unmapped_columns=unmapped,
        missing_required_fields=[],
        rows=results,
    )


def _key(value: str | None) -> str:
    return versions.normalize_name(value)


def _snapshot(product: ProductReference) -> tuple:
    return tuple(
        getattr(product, name) for name in PRODUCT_FIELD_LABELS if name not in ("vendor", "model")
    )


# ---------------------------------------------------------------------------
# 脆弱性データの取り込み（オフライン JSON）
# ---------------------------------------------------------------------------


@dataclass
class VulnerabilityImportResult:
    format: str
    total: int
    created: int
    updated: int
    skipped: int
    affects_created: int
    messages: list[str] = field(default_factory=list)


def import_vulnerabilities(
    db: Session, payload: bytes | str, *, fetched_at: date | None = None
) -> VulnerabilityImportResult:
    """NVD 2.0 形式 / CISA KEV 形式 / 独自の簡易形式の JSON を取り込む。

    形式は中身を見て判別する。閉域環境では「落としてきたファイルを置くだけ」で
    済むようにしておきたいため、ファイル名や指定に依存しない。
    """
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON として読めませんでした: {exc}") from exc

    fetched_at = fetched_at or date.today()

    if isinstance(data, dict) and "vulnerabilities" in data and "cveID" not in text[:200]:
        if data.get("vulnerabilities") and "cve" in (data["vulnerabilities"][0] or {}):
            return _import_nvd(db, data, fetched_at)
    if isinstance(data, dict) and "vulnerabilities" in data:
        return _import_kev(db, data, fetched_at)
    if isinstance(data, list):
        return _import_simple(db, data, fetched_at)

    raise ValueError("対応していない形式です（NVD 2.0 / CISA KEV / 簡易形式に対応しています）")


def _upsert_vulnerability(db: Session, cve_id: str) -> tuple[Vulnerability, bool]:
    found = db.scalar(select(Vulnerability).where(Vulnerability.cve_id == cve_id))
    if found is not None:
        return found, False
    created = Vulnerability(cve_id=cve_id)
    db.add(created)
    return created, True


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _import_nvd(db: Session, data: dict, fetched_at: date) -> VulnerabilityImportResult:
    """NIST NVD 2.0 の JSON。CPE の設定から影響範囲を組み立てる。"""
    created = updated = skipped = affects = 0

    for entry in data.get("vulnerabilities", []):
        cve = entry.get("cve") or {}
        cve_id = cve.get("id")
        if not cve_id:
            skipped += 1
            continue

        vulnerability, is_new = _upsert_vulnerability(db, cve_id)
        created, updated = (created + 1, updated) if is_new else (created, updated + 1)

        descriptions = cve.get("descriptions") or []
        english = next((d for d in descriptions if d.get("lang") == "en"), None)
        if english:
            vulnerability.description = english.get("value")
            vulnerability.title = (english.get("value") or "")[:300] or None

        metrics = cve.get("metrics") or {}
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            entries = metrics.get(key) or []
            if entries:
                cvss = (entries[0] or {}).get("cvssData") or {}
                vulnerability.cvss_score = cvss.get("baseScore")
                vulnerability.cvss_severity = cvss.get("baseSeverity") or (
                    entries[0].get("baseSeverity")
                )
                break

        vulnerability.published = _parse_iso(cve.get("published"))
        vulnerability.last_modified = _parse_iso(cve.get("lastModified"))
        vulnerability.source = ReferenceSource.NVD
        vulnerability.source_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        vulnerability.fetched_at = fetched_at

        vulnerability.affects.clear()
        db.flush()
        for affect in _affects_from_cpe(cve):
            vulnerability.affects.append(affect)
            affects += 1

    db.commit()
    return VulnerabilityImportResult(
        format="nvd",
        total=len(data.get("vulnerabilities", [])),
        created=created,
        updated=updated,
        skipped=skipped,
        affects_created=affects,
    )


def _affects_from_cpe(cve: dict) -> list[VulnerabilityAffect]:
    """CPE 2.3 の文字列から (ベンダー, 型番/製品) とバージョン範囲を取り出す。

    cpe:2.3:o:cisco:ios_xe:17.9.4:*:*:*:*:*:*:*
             ^part ^vendor ^product ^version
    """
    results: list[VulnerabilityAffect] = []

    for configuration in cve.get("configurations") or []:
        for node in configuration.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                if not match.get("vulnerable", True):
                    continue
                criteria = match.get("criteria") or ""
                parts = criteria.split(":")
                if len(parts) < 6:
                    continue

                vendor, product, version = parts[3], parts[4], parts[5]
                affect = VulnerabilityAffect(
                    vendor=vendor.replace("_", " "),
                    model=None,
                    os_name=product.replace("_", " "),
                    version_start_including=match.get("versionStartIncluding"),
                    version_start_excluding=match.get("versionStartExcluding"),
                    version_end_including=match.get("versionEndIncluding"),
                    version_end_excluding=match.get("versionEndExcluding"),
                    exact_version=version if version not in ("*", "-") else None,
                )
                results.append(affect)
    return results


def _import_kev(db: Session, data: dict, fetched_at: date) -> VulnerabilityImportResult:
    """CISA KEV（実際に悪用が確認された脆弱性）のカタログ。

    影響範囲の記述は粗いが、「悪用されている」という情報自体の価値が高いので、
    既存の CVE に印を付ける用途を主にしている。
    """
    created = updated = skipped = affects = 0

    for entry in data.get("vulnerabilities", []):
        cve_id = entry.get("cveID")
        if not cve_id:
            skipped += 1
            continue

        vulnerability, is_new = _upsert_vulnerability(db, cve_id)
        created, updated = (created + 1, updated) if is_new else (created, updated + 1)

        vulnerability.known_exploited = True
        vulnerability.title = entry.get("vulnerabilityName") or vulnerability.title
        vulnerability.description = entry.get("shortDescription") or vulnerability.description
        vulnerability.published = _parse_iso(entry.get("dateAdded")) or vulnerability.published
        vulnerability.remediation_due = _parse_iso(entry.get("dueDate"))
        vulnerability.fetched_at = fetched_at
        vulnerability.source_url = (
            vulnerability.source_url or f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        )
        # NVD 由来の詳細がある場合は出典を上書きしない
        if is_new:
            vulnerability.source = ReferenceSource.KEV

        vendor = (entry.get("vendorProject") or "").strip()
        product = (entry.get("product") or "").strip()
        if vendor and not any(a.vendor.lower() == vendor.lower() for a in vulnerability.affects):
            vulnerability.affects.append(
                VulnerabilityAffect(vendor=vendor, model=None, os_name=product or None)
            )
            affects += 1

    db.commit()
    return VulnerabilityImportResult(
        format="kev",
        total=len(data.get("vulnerabilities", [])),
        created=created,
        updated=updated,
        skipped=skipped,
        affects_created=affects,
        messages=["CISA KEV は影響範囲の記述が粗いため、機種の絞り込みは限定的です。"],
    )


def _import_simple(db: Session, data: list, fetched_at: date) -> VulnerabilityImportResult:
    """独自の簡易形式。ベンダー PSIRT を手で整理した場合に使う。

    [{"cve_id": "CVE-2024-1234", "cvss_score": 9.8, "affects":
        [{"vendor": "Cisco", "model": "C9500*", "os_name": "IOS-XE",
          "version_end_excluding": "17.9.5", "fixed_version": "17.9.5"}]}]
    """
    created = updated = skipped = affects = 0

    for entry in data:
        cve_id = (entry or {}).get("cve_id")
        if not cve_id:
            skipped += 1
            continue

        vulnerability, is_new = _upsert_vulnerability(db, cve_id)
        created, updated = (created + 1, updated) if is_new else (created, updated + 1)

        for name in ("title", "description", "cvss_score", "cvss_severity"):
            if entry.get(name) is not None:
                setattr(vulnerability, name, entry[name])
        if entry.get("known_exploited"):
            vulnerability.known_exploited = True
        vulnerability.published = _parse_iso(entry.get("published")) or vulnerability.published
        vulnerability.source = ReferenceSource(entry.get("source", "manual"))
        vulnerability.source_url = entry.get("source_url") or vulnerability.source_url
        vulnerability.fetched_at = fetched_at

        vulnerability.affects.clear()
        db.flush()
        for raw in entry.get("affects") or []:
            vulnerability.affects.append(
                VulnerabilityAffect(
                    vendor=raw.get("vendor") or "",
                    model=raw.get("model"),
                    os_name=raw.get("os_name"),
                    version_start_including=raw.get("version_start_including"),
                    version_start_excluding=raw.get("version_start_excluding"),
                    version_end_including=raw.get("version_end_including"),
                    version_end_excluding=raw.get("version_end_excluding"),
                    exact_version=raw.get("exact_version"),
                    fixed_version=raw.get("fixed_version"),
                )
            )
            affects += 1

    db.commit()
    return VulnerabilityImportResult(
        format="simple",
        total=len(data),
        created=created,
        updated=updated,
        skipped=skipped,
        affects_created=affects,
    )


# ---------------------------------------------------------------------------
# 突合
# ---------------------------------------------------------------------------


@dataclass
class DeviceVulnerability:
    device_id: int
    hostname: str
    cve_id: str
    title: str | None
    cvss_score: float | None
    cvss_severity: str | None
    known_exploited: bool
    fixed_version: str | None
    matched_on: str  # 何で一致したかを残す（説明できないと使えない）
    source: str
    fetched_at: date | None


def match_device(device: Device, vulnerabilities: list[Vulnerability]) -> list[DeviceVulnerability]:
    """1台の機器に該当する脆弱性を返す。"""
    results: list[DeviceVulnerability] = []

    for vulnerability in vulnerabilities:
        for affect in vulnerability.affects:
            reason = _affect_matches(device, affect)
            if reason is None:
                continue
            results.append(
                DeviceVulnerability(
                    device_id=device.id,
                    hostname=device.hostname,
                    cve_id=vulnerability.cve_id,
                    title=vulnerability.title,
                    cvss_score=vulnerability.cvss_score,
                    cvss_severity=vulnerability.cvss_severity,
                    known_exploited=vulnerability.known_exploited,
                    fixed_version=affect.fixed_version,
                    matched_on=reason,
                    source=str(vulnerability.source),
                    fetched_at=vulnerability.fetched_at,
                )
            )
            break  # 同じ CVE を複数回出さない

    # 悪用確認済みを先頭に、次に CVSS の高い順
    results.sort(key=lambda r: (not r.known_exploited, -(r.cvss_score or 0)))
    return results


def _affect_matches(device: Device, affect: VulnerabilityAffect) -> str | None:
    """一致したら「何で一致したか」の説明を返す。一致しなければ None。"""
    if versions.normalize_name(device.vendor) != versions.normalize_name(affect.vendor):
        return None
    if not versions.matches_model(affect.model, device.model):
        return None

    if affect.os_name and versions.normalize_name(device.os_name) != versions.normalize_name(
        affect.os_name
    ):
        # NVD の CPE は製品名（ios_xe）で来るため、OS 名が食い違うだけなら型番で救う
        if not affect.model:
            return None

    if affect.exact_version:
        if versions.equals(device.os_version, affect.exact_version):
            return f"バージョン {device.os_version} が該当"
        return None

    has_range = any(
        (
            affect.version_start_including,
            affect.version_start_excluding,
            affect.version_end_including,
            affect.version_end_excluding,
        )
    )
    if has_range:
        if versions.in_range(
            device.os_version,
            start_including=affect.version_start_including,
            start_excluding=affect.version_start_excluding,
            end_including=affect.version_end_including,
            end_excluding=affect.version_end_excluding,
        ):
            return f"バージョン {device.os_version} が影響範囲に含まれる"
        return None

    # バージョン指定が無い＝その製品すべてが対象。型番まで特定できている場合のみ採用する。
    if affect.model:
        return "型番が影響製品に一致（バージョン指定なし）"
    return None


def load_vulnerabilities(db: Session) -> list[Vulnerability]:
    return list(db.scalars(select(Vulnerability).options(selectinload(Vulnerability.affects))))
