from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.exposure import ExposureStatus
from app.schemas.config_plans import DiffRead


class ExposureRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    due_on: date | None
    overdue: bool
    note: str | None


class ExposureSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    unhandled: int
    known_exploited_unhandled: int
    overdue: int
    mitigated_but_still_affected: int
    reopened: int
    unverified: int
    by_status: dict[str, int]


class SyncResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created: int
    reopened: int
    resolved: int
    unchanged: int
    messages: list[str]


class SyncScheduleRead(BaseModel):
    """裏で回す洗い直しの状態。"""

    interval_hours: int
    running: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_trigger: str | None
    unhandled_before: int | None
    unhandled_after: int | None
    grew_by: int
    messages: list[str]


class ExposureDecision(BaseModel):
    """対処状況を人が決める。"""

    status: ExposureStatus
    remediation_id: int | None = None
    decided_by: str | None = None
    note: str | None = None


class FixConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    hostname: str
    cve_id: str
    remediation_id: int
    remediation_summary: str
    vendor: str
    source: str
    current_full: str
    fixed_full: str
    diff: DiffRead
    already_applied: bool
    notes: list[str]
