from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class ReplacementTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    hostname: str
    vendor: str | None
    model: str | None
    site_name: str | None
    reason: str
    due_on: date | None
    severity: str
    estimated_cost: float | None
    impact_count: int


class FiscalYearPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fiscal_year: int
    label: str
    device_count: int
    estimated_cost: float
    targets: list[ReplacementTargetRead]


class ReplacementPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evaluated_on: date
    years: list[FiscalYearPlanRead]
    unscheduled: list[ReplacementTargetRead]
    total_cost: float
    total_devices: int


class ImpactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    hostname: str
    directly_connected: list[str]
    isolated: list[str]
    note: str
