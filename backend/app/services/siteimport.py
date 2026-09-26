"""拠点台帳の取り込み。

拠点は数が多い。機器の台帳から名前だけは起きるが、住所・状態・開設日は
そこに入っていないので、拠点だけの表で一括して入れられる必要がある。
2万を超える拠点を画面から1つずつ直すのは現実的ではない。

書き出した表（拠点の書き出し）をそのまま直して戻せる並びにしてある。
突き合わせの鍵は拠点ID。IDが無い行は拠点名で探し、それも無ければ新しく起こす。

機器は触らない。拠点の行を消しても、その拠点の機器は残る（引き上げの
棚卸しに要る）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site
from app.models.site import SiteStatus
from app.services.exporter import SITE_STATUS_LABELS
from app.services.tablefile import read_table

#: 表の見出しの表記ゆれ。書き出した表と、現場の表のどちらも読めるように
ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("拠点id", "拠点コード", "site_code", "sitecode", "siteid", "code"),
    "name": ("拠点名", "拠点", "site", "sitename", "name", "設置場所"),
    "status": ("状態", "拠点状態", "status"),
    "address": ("住所", "所在地", "address"),
    "opened_on": ("開設日", "開設", "opened_on", "opened"),
    "closed_on": ("閉鎖日", "閉店日", "closed_on", "closed"),
    "notes": ("メモ", "備考", "notes", "note"),
}

#: 状態の書き方。表示名でも、内部の名前でも受ける
STATUS_FROM_LABEL = {label: value for value, label in SITE_STATUS_LABELS.items()}


@dataclass
class RowIssue:
    row_number: int
    message: str


@dataclass
class SiteImportResult:
    total_rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    issues: list[RowIssue] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    committed: bool = False


def _key(text: str) -> str:
    return "".join(str(text).split()).lower().replace("（", "").replace("）", "")


def _map_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    for header in headers:
        normalized = _key(header)
        for name, aliases in ALIASES.items():
            if name in mapping.values():
                continue
            if normalized in aliases or normalized == name:
                mapping[header] = name
                break
    return mapping, [h for h in headers if h not in mapping]


def _as_date(value: object) -> date | None:
    """日付の書き方は現場でまちまち。読めた形で受ける。

    Excel の日付のセルは datetime で来る。文字にしてから読むと「2018-04-01 00:00:00」
    になって読めず、書き出した拠点台帳をそのまま戻せなかった。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip().replace("/", "-").replace(".", "-")
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.date()
    raise ValueError(f"日付として読めません: {value}")


def _as_status(value: str) -> str:
    text = value.strip()
    if not text:
        return SiteStatus.ACTIVE
    if text in STATUS_FROM_LABEL:
        return STATUS_FROM_LABEL[text]
    lowered = text.lower()
    if lowered in {status.value for status in SiteStatus}:
        return lowered
    raise ValueError(f"状態として読めません: {value}（稼働中 / 開設予定 / 一時閉鎖 / 閉鎖・閉店）")


def run_import(
    db: Session, filename: str, content: bytes, *, commit: bool = False
) -> SiteImportResult:
    """拠点台帳を取り込む。commit=False なら中身を見るだけで DB は変えない。"""
    table = read_table(filename, content)
    mapping, unmapped = _map_columns(table.headers)
    result = SiteImportResult(total_rows=len(table.rows), unmapped_columns=unmapped)

    if "code" not in mapping.values() and "name" not in mapping.values():
        result.issues.append(RowIssue(0, "列が足りません: 拠点ID か 拠点名 のどちらかが要ります"))
        return result

    sites = list(db.scalars(select(Site)))
    by_code = {site.code: site for site in sites if site.code}
    by_name: dict[str, Site] = {}
    for site in sites:
        by_name.setdefault(site.name, site)

    for number, raw in enumerate(table.rows, start=table.first_row):
        values = {mapping[h]: str(raw.get(h) or "").strip() for h in mapping}
        #: 日付は元の値で読む（Excel の日付のセルは datetime で来る）
        cells = {mapping[h]: raw.get(h) for h in mapping}
        code, name = values.get("code", ""), values.get("name", "")
        if not code and not name:
            continue  # 空行は黙って飛ばす

        site = by_code.get(code) if code else None
        if site is None and name:
            found = by_name.get(name)
            # 名前が同じでも、別の拠点IDが振ってあるなら別の拠点。
            # 同名のビル・同名の営業所は普通にあるので、混ぜてはいけない
            if found is not None and (not code or not found.code):
                site = found

        try:
            changes: dict[str, object] = {}
            if name:
                changes["name"] = name
            if code:
                changes["code"] = code
            if "address" in values:
                changes["address"] = values["address"] or None
            if "notes" in values:
                changes["notes"] = values["notes"] or None
            if values.get("status"):
                changes["status"] = _as_status(values["status"])
            if "opened_on" in values:
                changes["opened_on"] = _as_date(cells["opened_on"])
            if "closed_on" in values:
                changes["closed_on"] = _as_date(cells["closed_on"])
        except ValueError as error:
            result.issues.append(RowIssue(number, str(error)))
            continue

        if site is None:
            if not name:
                result.issues.append(RowIssue(number, "新しい拠点には拠点名が要ります"))
                continue
            site = Site(**changes)  # type: ignore[arg-type]
            db.add(site)
            by_name.setdefault(name, site)
            if code:
                by_code[code] = site
            result.created += 1
            continue

        touched = [
            field_name
            for field_name, value in changes.items()
            if getattr(site, field_name) != value
        ]
        if not touched:
            result.unchanged += 1
            continue
        for field_name in touched:
            setattr(site, field_name, changes[field_name])
        if code:
            by_code[code] = site
        result.updated += 1

    if commit:
        db.commit()
        result.committed = True
    else:
        db.rollback()
    return result
