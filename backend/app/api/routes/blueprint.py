"""条件から構成を起こす口。

「A と B を、こういう条件で繋ぐならどうなるか」に答える。案を見るだけの口と、
テスト拠点として台帳に置いてみる口を分けてある。見るだけのつもりで台帳が
増えると、本物と混ざる。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import blueprint as service

router = APIRouter(tags=["blueprint"])

DbSession = Annotated[Session, Depends(get_db)]


class BlueprintBody(BaseModel):
    a_name: str = Field(default="A", description="つなぐ片方の呼び名")
    b_name: str = Field(default="B", description="もう片方の呼び名")
    conditions: list[str] = Field(default_factory=list, description="条件の名前")
    base: str | None = Field(default=None, description="案に使うアドレス帯（例 10.90.0.0/16）")
    vendor: str = "Cisco"
    prefix: str = Field(default="test", description="機器名の頭に付ける印")


class ConditionRead(BaseModel):
    name: str
    label: str


class DeviceRead(BaseModel):
    role: str
    hostname: str
    device_type: str
    purpose: str


class LinkRead(BaseModel):
    a_hostname: str
    a_interface: str
    b_hostname: str
    b_interface: str
    description: str


class SegmentRead(BaseModel):
    name: str
    vlan_id: int | None
    subnet: str
    gateway: str | None
    members: list[str]


class RouteRead(BaseModel):
    hostname: str
    destination: str
    next_hop: str


class DecisionRead(BaseModel):
    topic: str
    choice: str
    why: str


class BlueprintRead(BaseModel):
    title: str
    conditions: list[ConditionRead]
    devices: list[DeviceRead]
    links: list[LinkRead]
    segments: list[SegmentRead]
    routes: list[RouteRead]
    decisions: list[DecisionRead]
    warnings: list[str]
    configs: dict[str, str]
    notes: dict[str, list[str]]


class TestSiteRead(BaseModel):
    id: int
    name: str
    devices: int


class AppliedRead(BaseModel):
    site_id: int
    site_name: str
    device_count: int
    link_count: int


@router.get("/blueprint/conditions", response_model=list[ConditionRead])
def list_conditions() -> list[ConditionRead]:
    """指定できる条件。画面のチェックはこれを並べる。"""
    return [
        ConditionRead(name=name, label=label) for name, label in service.CONDITION_LABELS.items()
    ]


def _read(plan: service.Blueprint) -> BlueprintRead:
    return BlueprintRead(
        title=plan.title,
        conditions=[
            ConditionRead(name=name, label=service.CONDITION_LABELS[name])
            for name in plan.conditions
        ],
        devices=[
            DeviceRead(
                role=d.role,
                hostname=d.hostname,
                device_type=str(d.device_type),
                purpose=d.purpose,
            )
            for d in plan.devices
        ],
        links=[
            LinkRead(
                a_hostname=link.a_hostname,
                a_interface=link.a_interface,
                b_hostname=link.b_hostname,
                b_interface=link.b_interface,
                description=link.description,
            )
            for link in plan.links
        ],
        segments=[
            SegmentRead(
                name=s.name,
                vlan_id=s.vlan_id,
                subnet=s.subnet,
                gateway=s.gateway,
                members=s.members,
            )
            for s in plan.segments
        ],
        routes=[
            RouteRead(hostname=r.hostname, destination=r.destination, next_hop=r.next_hop)
            for r in plan.routes
        ],
        decisions=[DecisionRead(topic=d.topic, choice=d.choice, why=d.why) for d in plan.decisions],
        warnings=plan.warnings,
        configs=plan.configs,
        notes=plan.notes,
    )


def _request(payload: BlueprintBody) -> service.BlueprintRequest:
    unknown = [c for c in payload.conditions if c not in service.CONDITION_LABELS]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"知らない条件です: {', '.join(unknown)}",
        )
    return service.BlueprintRequest(
        a_name=payload.a_name.strip() or "A",
        b_name=payload.b_name.strip() or "B",
        conditions=payload.conditions,
        base=payload.base,
        vendor=payload.vendor,
        prefix=payload.prefix.strip(),
    )


@router.post("/blueprint", response_model=BlueprintRead)
def draw(payload: BlueprintBody) -> BlueprintRead:
    """案を出すだけ。台帳は触らない。"""
    return _read(service.build(_request(payload)))


@router.post("/blueprint/apply", response_model=AppliedRead)
def apply(payload: BlueprintBody, db: DbSession) -> AppliedRead:
    """案をテスト拠点として台帳に置く。そのままトポロジー図で見られる。

    本物と混ざらないよう、拠点名に印を付ける。まとめて消せる。
    """
    plan = service.build(_request(payload))
    try:
        site = service.apply_to_ledger(db, plan, f"{payload.a_name}-{payload.b_name}")
    except service.HostnameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AppliedRead(
        site_id=site.id,
        site_name=site.name,
        device_count=len(plan.devices),
        link_count=len(plan.links),
    )


@router.get("/blueprint/test-sites", response_model=list[TestSiteRead])
def list_test_sites(db: DbSession) -> list[TestSiteRead]:
    """作ってあるテスト拠点。消すときの手がかりにする。"""
    return [
        TestSiteRead(id=site.id, name=site.name, devices=len(site.devices))
        for site in service.test_sites(db)
    ]


@router.delete("/blueprint/test-sites/{site_id}", response_model=dict)
def delete_test_site(site_id: int, db: DbSession) -> dict:
    """テスト拠点を、機器ごと消す。印の付いた拠点だけ消せる。"""
    try:
        removed = service.remove_test_site(db, site_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"removed_devices": removed}
