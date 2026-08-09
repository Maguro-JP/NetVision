"""機器 × 脆弱性 の対処状況を、取りこぼさずに追い切る。

機器を1台ずつ開いて確認する運用は必ず漏れる。漏れる経路は主に4つ。

1. 全体で何件残っているのか見えない
2. 「対処を書いた」と「その機器に実際に入れた」が同じに見える
3. 回避策を当てても機器のバージョンは変わらないので該当は残る。
   恒久対処と同じ「済み」にすると、直っていない機器が永久に見えなくなる
4. 状況が変わっても誰も気付かない
   （CVEを取り込んだ / 機器の版が変わった / 機器が増えた）

そこで機器×CVEを1行として持ち、状態を明示的に置く。
判定に使った時点のバージョンを覚えておき、変わったら差し戻す。
「もう該当しない」は自動で閉じるが、人が閉じた判断は自動で消さない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Device,
    DeviceExposure,
    DeviceStatus,
    ExposureStatus,
    Vulnerability,
)
from app.models.exposure import UNHANDLED
from app.services.device_query import DeviceFilters, query_devices
from app.services.reference import match_device

# 人が判断して閉じた状態。自動処理で勝手に書き換えない
HUMAN_DECIDED = {
    ExposureStatus.MITIGATED,
    ExposureStatus.FIXED,
    ExposureStatus.ACCEPTED,
    ExposureStatus.NOT_AFFECTED,
}


@dataclass
class SyncResult:
    created: int = 0  # 新しく見つかった該当
    reopened: int = 0  # 状況が変わって差し戻した
    resolved: int = 0  # 該当しなくなった
    unchanged: int = 0
    messages: list[str] = field(default_factory=list)


def _active_devices(db: Session) -> list[Device]:
    return [d for d in query_devices(db, DeviceFilters()) if d.status is not DeviceStatus.RETIRED]


def sync(db: Session, *, today: date | None = None) -> SyncResult:
    """該当状況を洗い直して、台帳に反映する。

    CVE を取り込んだ後、機器を登録・更新した後、定期的に呼ぶ。
    ここを通っていない限り「未対応ゼロ」は信用できないので、
    最終実行日時を画面に出す前提で作っている。
    """
    today = today or date.today()
    result = SyncResult()

    vulnerabilities = list(db.scalars(select(Vulnerability)))
    devices = _active_devices(db)

    existing = {(e.device_id, e.vulnerability_id): e for e in db.scalars(select(DeviceExposure))}
    by_cve = {v.cve_id: v for v in vulnerabilities}
    seen: set[tuple[int, int]] = set()

    for device in devices:
        for finding in match_device(device, vulnerabilities):
            vulnerability = by_cve.get(finding.cve_id)
            if vulnerability is None:
                continue

            key = (device.id, vulnerability.id)
            seen.add(key)
            row = existing.get(key)

            if row is None:
                db.add(
                    DeviceExposure(
                        device_id=device.id,
                        vulnerability_id=vulnerability.id,
                        status=ExposureStatus.OPEN,
                        first_seen_on=today,
                        decided_os_version=None,
                    )
                )
                result.created += 1
                continue

            # 一度「該当しなくなった」ものが再び該当した（版を戻した等）
            if row.status is ExposureStatus.RESOLVED:
                row.status = ExposureStatus.OPEN
                row.reopened_on = today
                row.reopen_reason = "再び影響範囲に入りました"
                result.reopened += 1
                continue

            # 判断したときと機器のバージョンが変わっている
            if (
                row.status in HUMAN_DECIDED
                and row.decided_os_version is not None
                and row.decided_os_version != (device.os_version or "")
            ):
                previous = row.status
                row.status = ExposureStatus.OPEN
                row.reopened_on = today
                row.reopen_reason = (
                    f"判断した時点のバージョン {row.decided_os_version} から "
                    f"{device.os_version} に変わりました（前の判断: {previous}）"
                )
                result.reopened += 1
                continue

            result.unchanged += 1

    # 該当しなくなったもの
    for (device_id, vulnerability_id), row in existing.items():
        if (device_id, vulnerability_id) in seen:
            continue
        if row.status is ExposureStatus.RESOLVED:
            continue
        row.status = ExposureStatus.RESOLVED
        row.note = (row.note or "") + f"\n[{today}] 影響範囲から外れました"
        result.resolved += 1

    db.commit()

    if result.created:
        result.messages.append(f"新しく {result.created} 件の該当が見つかりました")
    if result.reopened:
        result.messages.append(f"{result.reopened} 件は状況が変わったため差し戻しました")
    if result.resolved:
        result.messages.append(f"{result.resolved} 件は該当しなくなりました")
    if not result.messages:
        result.messages.append("変化はありません")
    return result


# ---------------------------------------------------------------------------
# 一覧と集計
# ---------------------------------------------------------------------------


@dataclass
class ExposureRow:
    id: int
    device_id: int
    hostname: str
    os_version: str | None
    cve_id: str
    title: str | None
    cvss_score: float | None
    known_exploited: bool
    status: str
    remediation_id: int | None
    remediation_summary: str | None
    decided_by: str | None
    decided_on: date | None
    first_seen_on: date | None
    reopened_on: date | None
    reopen_reason: str | None
    config_verified: bool | None
    config_verified_on: date | None
    due_on: date | None  # KEV の是正期限
    overdue: bool
    note: str | None


@dataclass
class ExposureSummary:
    total: int = 0
    unhandled: int = 0  # 未対応 + 対応中
    known_exploited_unhandled: int = 0  # 悪用確認済みで未対応（最優先）
    overdue: int = 0  # KEV の是正期限を過ぎている
    mitigated_but_still_affected: int = 0  # 暫定対処のみ。恒久対処が残っている
    reopened: int = 0
    unverified: int = 0  # 済みだが config で裏が取れていない
    by_status: dict[str, int] = field(default_factory=dict)


def _to_row(exposure: DeviceExposure, today: date) -> ExposureRow:
    vulnerability = exposure.vulnerability
    due = vulnerability.remediation_due if vulnerability else None
    unhandled = exposure.status in UNHANDLED

    return ExposureRow(
        id=exposure.id,
        device_id=exposure.device_id,
        hostname=exposure.device.hostname if exposure.device else "—",
        os_version=exposure.device.os_version if exposure.device else None,
        cve_id=vulnerability.cve_id if vulnerability else "—",
        title=vulnerability.title if vulnerability else None,
        cvss_score=vulnerability.cvss_score if vulnerability else None,
        known_exploited=bool(vulnerability and vulnerability.known_exploited),
        status=str(exposure.status),
        remediation_id=exposure.remediation_id,
        remediation_summary=exposure.remediation.summary if exposure.remediation else None,
        decided_by=exposure.decided_by,
        decided_on=exposure.decided_on,
        first_seen_on=exposure.first_seen_on,
        reopened_on=exposure.reopened_on,
        reopen_reason=exposure.reopen_reason,
        config_verified=exposure.config_verified,
        config_verified_on=exposure.config_verified_on,
        due_on=due,
        overdue=bool(due and due < today and unhandled),
        note=exposure.note,
    )


def listing(
    db: Session,
    *,
    status: str | None = None,
    device_id: int | None = None,
    unhandled_only: bool = False,
    today: date | None = None,
) -> list[ExposureRow]:
    today = today or date.today()
    stmt = select(DeviceExposure)
    if device_id is not None:
        stmt = stmt.where(DeviceExposure.device_id == device_id)
    if status:
        stmt = stmt.where(DeviceExposure.status == status)

    rows = [_to_row(e, today) for e in db.scalars(stmt)]
    if unhandled_only:
        rows = [r for r in rows if r.status in {str(s) for s in UNHANDLED}]

    # 悪用確認済みで未対応 → 期限超過 → CVSS の順。上から潰せば良い並びにする
    rows.sort(
        key=lambda r: (
            not (r.known_exploited and r.status in {str(s) for s in UNHANDLED}),
            not r.overdue,
            -(r.cvss_score or 0),
            r.hostname,
        )
    )
    return rows


def summary(db: Session, *, today: date | None = None) -> ExposureSummary:
    today = today or date.today()
    rows = listing(db, today=today)
    unhandled_names = {str(s) for s in UNHANDLED}

    out = ExposureSummary(total=len(rows))
    for row in rows:
        out.by_status[row.status] = out.by_status.get(row.status, 0) + 1

        if row.status in unhandled_names:
            out.unhandled += 1
            if row.known_exploited:
                out.known_exploited_unhandled += 1
        if row.overdue:
            out.overdue += 1
        if row.status == str(ExposureStatus.MITIGATED):
            # 回避策だけ。機器の版は変わっていないので該当は残ったまま
            out.mitigated_but_still_affected += 1
        if row.reopened_on and row.status in unhandled_names:
            out.reopened += 1
        if row.status in {str(ExposureStatus.MITIGATED), str(ExposureStatus.FIXED)} and not (
            row.config_verified
        ):
            out.unverified += 1

    return out
