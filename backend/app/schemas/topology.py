from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.topology import LinkOrigin
from app.services.configparse import Vendor
from app.services.lifecycle import Severity


class FieldDiffRead(BaseModel):
    field: str
    label: str
    current: str | None
    observed: str | None
    differs: bool


class UnresolvedNeighborRead(BaseModel):
    local_device: str
    local_interface: str
    remote_hostname: str
    remote_platform: str | None


class IngestResultRead(BaseModel):
    filename: str
    vendor: Vendor
    vendor_label: str
    hostname: str | None
    device_id: int | None
    device_hostname: str | None
    matched: bool
    created: bool = False
    unchanged: bool
    snapshot_id: int | None
    interface_count: int
    neighbor_count: int
    vlan_count: int
    links_created: int
    diffs: list[FieldDiffRead]
    applied_fields: list[str]
    unresolved_neighbors: list[UnresolvedNeighborRead]
    message: str | None


class ConfigSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int | None
    hostname: str | None
    filename: str
    sha256: str
    vendor: str | None
    parsed_os_name: str | None
    parsed_os_version: str | None
    parsed_model: str | None
    parsed_serial: str | None
    interface_count: int
    neighbor_count: int
    created_at: datetime


class ConfigSnapshotDetail(ConfigSnapshotRead):
    content: str


class ConfigDiffRead(BaseModel):
    from_id: int
    to_id: int
    diff: str
    changed: bool


class InterfaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    name: str
    description: str | None
    ip_address: str | None
    vlan: int | None
    mode: str | None
    enabled: bool


class TopologyNodeRead(BaseModel):
    device_id: int
    hostname: str
    vendor: str
    model: str
    device_type: str
    status: str
    site_name: str | None
    mgmt_ip: str | None
    severity: Severity
    interface_count: int
    x: float
    y: float
    placed: bool


class TopologyEdgeRead(BaseModel):
    id: int
    source: int
    target: int
    source_interface: str
    target_interface: str
    origin: LinkOrigin
    protocol: str | None
    label: str | None
    confirmed_both_ways: bool


class TopologyGraphRead(BaseModel):
    layer: str
    nodes: list[TopologyNodeRead]
    edges: list[TopologyEdgeRead]
    unplaced_count: int


class NodePosition(BaseModel):
    device_id: int
    x: float
    y: float


class SavePositions(BaseModel):
    layer: str = "physical"
    positions: list[NodePosition]


class LinkCreate(BaseModel):
    a_device_id: int
    b_device_id: int
    a_interface: str = Field(default="", max_length=120)
    b_interface: str = Field(default="", max_length=120)
    label: str | None = None


class LinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a_interface: str | None = Field(default=None, max_length=120)
    b_interface: str | None = Field(default=None, max_length=120)
    label: str | None = None


class LinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    a_device_id: int
    a_interface: str
    b_device_id: int
    b_interface: str
    origin: LinkOrigin
    protocol: str | None
    label: str | None
    confirmed_both_ways: bool


class TopologyStatsRead(BaseModel):
    device_count: int
    link_count: int
    discovered_links: int
    manual_links: int
    confirmed_links: int
    isolated_devices: list[str]


class RebuildResult(BaseModel):
    links_created: int
    unresolved_neighbors: list[UnresolvedNeighborRead]
