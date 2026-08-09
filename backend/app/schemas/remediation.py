from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models.remediation import RemediationKind
from app.schemas.types import Timestamp


class RemediationBase(BaseModel):
    kind: RemediationKind = RemediationKind.WORKAROUND
    vendor: str | None = None
    model: str | None = None
    os_name: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    summary: str = Field(min_length=1, max_length=300)
    fixed_version: str | None = None
    procedure: str | None = None
    source_document_id: int | None = None
    config_template_id: int | None = None
    verified_by: str | None = None
    verified_on: date | None = None
    notes: str | None = None


class RemediationCreate(RemediationBase):
    cve_id: str


class RemediationUpdate(BaseModel):
    kind: RemediationKind | None = None
    vendor: str | None = None
    model: str | None = None
    os_name: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=300)
    fixed_version: str | None = None
    procedure: str | None = None
    source_document_id: int | None = None
    config_template_id: int | None = None
    verified_by: str | None = None
    verified_on: date | None = None
    notes: str | None = None


class RemediationRead(RemediationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vulnerability_id: int
    cve_id: str | None = None
    source_document_title: str | None = None
    config_template_name: str | None = None
    created_at: Timestamp
    updated_at: Timestamp


class RemediationOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class SuggestionRead(BaseModel):
    id: int
    title: str | None = None
    name: str | None = None
    reason: str


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cve_id: str
    title: str | None
    cvss_score: float | None
    cvss_severity: str | None
    known_exploited: bool
    matched_on: str
    fixed_version: str | None
    upgrade_available: bool
    has_remediation: bool
    remediations: list[RemediationOptionRead]
    suggested_documents: list[SuggestionRead]
    suggested_templates: list[SuggestionRead]


class DeviceRemediationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    hostname: str
    os_version: str | None
    findings: list[FindingRead]
    unresolved_count: int
    note: str
