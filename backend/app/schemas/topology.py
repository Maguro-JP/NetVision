from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.topology import LinkOrigin
from app.schemas.types import Timestamp
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


class VlanRead(BaseModel):
    """図の絞り込みに使う VLAN 1つ分。台帳と実機の観測値を合わせたもの。"""

    model_config = ConfigDict(from_attributes=True)

    vlan_id: int
    name: str | None
    purpose: str | None = None
    owner: str | None = None
    subnet: str | None = None
    in_catalog: bool = False
    observed_names: list[str] = []
    device_ids: list[int]
    device_names: list[str]
    defined_device_ids: list[int] = []
    access_port_count: int
    trunk_port_count: int = 0
    native_port_count: int = 0


class VrfRead(BaseModel):
    """機器の中に切られた仮想ルータ。名前でまとめてある。"""

    model_config = ConfigDict(from_attributes=True)

    name: str
    rd: str | None
    description: str | None
    device_ids: list[int]
    device_names: list[str]
    interface_count: int
    rd_mismatch: bool


class VlanCatalogRead(BaseModel):
    """VLAN 台帳の1行。人が用途を書く側。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    vlan_id: int
    name: str | None
    purpose: str | None
    owner: str | None
    subnet: str | None
    notes: str | None


class VlanCatalogWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    purpose: str | None = Field(default=None, max_length=255)
    owner: str | None = Field(default=None, max_length=120)
    subnet: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class VlanCatalogCreate(VlanCatalogWrite):
    vlan_id: int = Field(ge=1, le=4094)


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
    created_at: Timestamp


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
    native_vlan: int | None = None
    trunk_vlans: str | None = None
    vrf: str | None = None
    enabled: bool


class TopologyNodeRead(BaseModel):
    device_id: int
    hostname: str
    vendor: str
    model: str
    device_type: str
    status: str
    site_id: int | None = None
    site_name: str | None
    mgmt_ip: str | None
    severity: Severity
    interface_count: int
    x: float
    y: float
    placed: bool
    vlans: list[int] = []
    vrfs: list[str] = []
    port_kinds: list[str] = []
    outside: bool = False
    hidden_peers: int = 0
    peer_kind: str | None = None
    plane: str = "access"


class TopologyGroupRead(BaseModel):
    id: int
    name: str
    plane: str
    color: str
    device_ids: list[int]


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
    vlans: list[int] = []
    vlan_mismatch: list[int] = []
    native_vlan: int | None = None
    native_mismatch: bool = False
    vrf: str | None = None
    source_side: str | None = None
    target_side: str | None = None


class TopologyGraphRead(BaseModel):
    layer: str
    nodes: list[TopologyNodeRead]
    edges: list[TopologyEdgeRead]
    unplaced_count: int
    groups: list[TopologyGroupRead] = []


class NodePosition(BaseModel):
    device_id: int
    x: float
    y: float


class SavePositions(BaseModel):
    layer: str = "physical"
    # どの拠点の図として残すか。空は全体図
    site_id: int | None = None
    positions: list[NodePosition]


class ArrangePositions(BaseModel):
    """整頓の起点。空ならサーバ上の保存済み（無ければ自動配置）から整える。"""

    positions: list[NodePosition] = []


class LinkCreate(BaseModel):
    a_device_id: int
    b_device_id: int
    a_interface: str = Field(default="", max_length=120)
    b_interface: str = Field(default="", max_length=120)
    label: str | None = None


SIDES = ("top", "bottom", "left", "right")


class LinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # つなぎ替え。図の上で線の端を別の機器へ持っていったときに使う
    a_device_id: int | None = None
    b_device_id: int | None = None
    a_interface: str | None = Field(default=None, max_length=120)
    b_interface: str | None = Field(default=None, max_length=120)
    label: str | None = None
    # 図の上でどの辺から線を出すか。null に戻すと自動に任せる
    a_side: Literal["top", "bottom", "left", "right"] | None = None
    b_side: Literal["top", "bottom", "left", "right"] | None = None


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
    a_side: str | None = None
    b_side: str | None = None


class TopologyStatsRead(BaseModel):
    device_count: int
    link_count: int
    discovered_links: int
    manual_links: int
    confirmed_links: int
    #: 繋がっていない機器の名前。多いときは頭から少しだけ返す
    isolated_devices: list[str]
    #: 繋がっていない機器の総数
    isolated_count: int = 0


class RebuildResult(BaseModel):
    links_created: int
    unresolved_neighbors: list[UnresolvedNeighborRead]


class GroupCreate(BaseModel):
    layer: str = "physical"
    name: str = Field(min_length=1, max_length=120)
    device_ids: list[int]
    plane: str = "custom"
    color: str | None = Field(default=None, max_length=16)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    plane: str | None = None
    color: str | None = Field(default=None, max_length=16)
    device_ids: list[int] | None = None
