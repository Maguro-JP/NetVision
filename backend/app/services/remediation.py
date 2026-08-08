"""CVE から対処までを1本につなぐ。

脆弱性を「見つける」ところまでは公開データ（NVD / CISA KEV）でできる。
できないのは**そこから先**で、「じゃあどうするか」は公開データに機械可読な
形で入っていない。ベンダーのアドバイザリに文章で書いてあるだけ。

なのでここは全自動にせず、こう作る。

- 機械にできること … 該当の判定、修正版の推定、その機器で引ける資料と
  テンプレートの提示
- 人がやること   … 回避策の手順を1回書く

一度書いた対処は3軸（ベンダー / 型番 / バージョン範囲）で保存されるので、
同じ型番・同じバージョン帯の機器すべてで再利用される。
「一度調べた対処を二度と調べ直さなくていい」がここの狙い。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Remediation, Vulnerability
from app.services import documents as docs
from app.services import versions
from app.services.reference import DeviceVulnerability, match_device


@dataclass
class RemediationOption:
    """1つの CVE に対する対処案。"""

    id: int
    kind: str
    summary: str
    fixed_version: str | None
    procedure: str | None
    source_document_id: int | None
    source_document_title: str | None
    config_template_id: int | None
    config_template_name: str | None
    verified_by: str | None
    verified_on: str | None
    matched_on: str


@dataclass
class FindingWithRemediation:
    """該当した脆弱性と、その対処。"""

    cve_id: str
    title: str | None
    cvss_score: float | None
    cvss_severity: str | None
    known_exploited: bool
    matched_on: str
    fixed_version: str | None  # 影響範囲から推定した修正版
    upgrade_available: bool
    remediations: list[RemediationOption] = field(default_factory=list)
    # 対処が1件も無いときに、手掛かりとして出すもの
    suggested_documents: list[dict] = field(default_factory=list)
    suggested_templates: list[dict] = field(default_factory=list)

    @property
    def has_remediation(self) -> bool:
        return bool(self.remediations)


@dataclass
class DeviceRemediationReport:
    device_id: int
    hostname: str
    os_version: str | None
    findings: list[FindingWithRemediation] = field(default_factory=list)
    unresolved_count: int = 0  # 対処が未登録の件数
    note: str = ""


def _applies(device: Device, remediation: Remediation) -> str | None:
    """この対処がこの機器に当てはまるか。当てはまるなら理由を返す。"""
    if remediation.vendor and not versions.normalize_name(
        remediation.vendor
    ) == versions.normalize_name(device.vendor or ""):
        return None

    if remediation.model and not versions.matches_model(remediation.model, device.model or ""):
        return None

    if remediation.os_name and device.os_name:
        if versions.normalize_name(remediation.os_name) != versions.normalize_name(device.os_name):
            return None

    applicability = docs.applies_to_version(
        device.os_version, remediation.applies_from, remediation.applies_to
    )
    if not applicability:
        return None

    if remediation.applies_from or remediation.applies_to:
        return f"稼働中の {device.os_version} が対処の適用範囲に入る"
    if remediation.model:
        return f"型番 {device.model} 向けの対処"
    return "この機器に当てはまる対処"


def _to_option(remediation: Remediation, reason: str) -> RemediationOption:
    return RemediationOption(
        id=remediation.id,
        kind=str(remediation.kind),
        summary=remediation.summary,
        fixed_version=remediation.fixed_version,
        procedure=remediation.procedure,
        source_document_id=remediation.source_document_id,
        source_document_title=(
            remediation.source_document.title if remediation.source_document else None
        ),
        config_template_id=remediation.config_template_id,
        config_template_name=(
            remediation.config_template.name if remediation.config_template else None
        ),
        verified_by=remediation.verified_by,
        verified_on=remediation.verified_on.isoformat() if remediation.verified_on else None,
        matched_on=reason,
    )


def _upgrade_is_available(device: Device, fixed_version: str | None) -> bool:
    """修正版が今のバージョンより新しいか。"""
    if not fixed_version or not device.os_version:
        return False
    return versions.compare(fixed_version, device.os_version) > 0


def for_device(db: Session, device: Device) -> DeviceRemediationReport:
    """1台の機器について、該当CVEとその対処をまとめる。"""
    vulnerabilities = list(db.scalars(select(Vulnerability)))
    findings: list[DeviceVulnerability] = match_device(device, vulnerabilities)

    by_cve = {v.cve_id: v for v in vulnerabilities}
    remediations = list(db.scalars(select(Remediation)))
    by_vulnerability: dict[int, list[Remediation]] = {}
    for remediation in remediations:
        by_vulnerability.setdefault(remediation.vulnerability_id, []).append(remediation)

    report = DeviceRemediationReport(
        device_id=device.id, hostname=device.hostname, os_version=device.os_version
    )

    for finding in findings:
        vulnerability = by_cve.get(finding.cve_id)
        options: list[RemediationOption] = []

        for remediation in by_vulnerability.get(vulnerability.id if vulnerability else -1, []):
            reason = _applies(device, remediation)
            if reason is not None:
                options.append(_to_option(remediation, reason))

        # 確認済みのものを先に、次に「上げる」より「今すぐ効く回避策」を先に出す
        options.sort(key=lambda o: (o.verified_on is None, o.kind != "workaround"))

        entry = FindingWithRemediation(
            cve_id=finding.cve_id,
            title=finding.title,
            cvss_score=finding.cvss_score,
            cvss_severity=finding.cvss_severity,
            known_exploited=finding.known_exploited,
            matched_on=finding.matched_on,
            fixed_version=finding.fixed_version,
            upgrade_available=_upgrade_is_available(device, finding.fixed_version),
            remediations=options,
        )

        if not options:
            # 対処が未登録。せめて調べる手掛かりを出す
            report.unresolved_count += 1
            entry.suggested_documents = [
                {"id": m.document.id, "title": m.document.title, "reason": m.reason}
                for m in docs.documents_for_device(db, device)[:3]
            ]
            entry.suggested_templates = [
                {"id": m.template.id, "name": m.template.name, "reason": m.reason}
                for m in docs.templates_for_device(db, device)[:3]
            ]

        report.findings.append(entry)

    if not findings:
        report.note = "該当する脆弱性はありません"
    elif report.unresolved_count:
        report.note = (
            f"{report.unresolved_count}件は対処が未登録です。"
            "調べた内容を登録しておくと、同じ型番・同じバージョン帯の機器すべてで再利用されます"
        )
    else:
        report.note = "該当したすべてに対処が登録されています"

    return report
