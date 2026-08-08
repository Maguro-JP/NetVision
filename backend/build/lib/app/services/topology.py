"""トポロジ図の組み立て。

自動推定した結線を土台に、人が配置を直して図を仕上げる前提で作っている。
- 位置は保存され、機器が増えても既存の配置は動かさない
- 未配置のノードだけ、種別を段にした自動レイアウトで初期位置を与える
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceType
from app.models.topology import Interface, Link, LinkOrigin, TopologyNode
from app.services.lifecycle import Severity, evaluate

DEFAULT_LAYER = "physical"

# 段の順序。上位（外部に近い）ほど上に置く。
TIER_ORDER: dict[DeviceType, int] = {
    DeviceType.ROUTER: 0,
    DeviceType.FIREWALL: 1,
    DeviceType.LOAD_BALANCER: 2,
    DeviceType.SWITCH: 3,
    DeviceType.WIRELESS_CONTROLLER: 4,
    DeviceType.WIRELESS_AP: 5,
    DeviceType.SERVER: 6,
    DeviceType.OTHER: 7,
}

_X_SPACING = 240.0
_Y_SPACING = 160.0


@dataclass
class TopologyNodeView:
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
    placed: bool  # 保存済みの位置か、自動配置か


@dataclass
class TopologyEdgeView:
    id: int
    source: int
    target: int
    source_interface: str
    target_interface: str
    origin: LinkOrigin
    protocol: str | None
    label: str | None
    confirmed_both_ways: bool


@dataclass
class TopologyGraph:
    layer: str
    nodes: list[TopologyNodeView] = field(default_factory=list)
    edges: list[TopologyEdgeView] = field(default_factory=list)
    unplaced_count: int = 0


def get_graph(
    db: Session, layer: str = DEFAULT_LAYER, *, include_retired: bool = False
) -> TopologyGraph:
    devices = list(
        db.scalars(
            select(Device).options(selectinload(Device.contracts), selectinload(Device.site))
        )
    )
    if not include_retired:
        devices = [d for d in devices if d.status is not DeviceStatus.RETIRED]

    visible_ids = {d.id for d in devices}

    positions = {
        node.device_id: node
        for node in db.scalars(select(TopologyNode).where(TopologyNode.layer == layer))
    }
    interface_counts = _interface_counts(db)

    placeholders = _auto_layout([d for d in devices if d.id not in positions])

    nodes: list[TopologyNodeView] = []
    for device in devices:
        saved = positions.get(device.id)
        x, y = (saved.x, saved.y) if saved else placeholders[device.id]
        nodes.append(
            TopologyNodeView(
                device_id=device.id,
                hostname=device.hostname,
                vendor=device.vendor,
                model=device.model,
                device_type=str(device.device_type),
                status=str(device.status),
                site_name=device.site.name if device.site else None,
                mgmt_ip=device.mgmt_ip,
                severity=evaluate(device).overall_severity,
                interface_count=interface_counts.get(device.id, 0),
                x=x,
                y=y,
                placed=saved is not None,
            )
        )

    edges = [
        TopologyEdgeView(
            id=link.id,
            source=link.a_device_id,
            target=link.b_device_id,
            source_interface=link.a_interface,
            target_interface=link.b_interface,
            origin=link.origin,
            protocol=link.protocol,
            label=link.label,
            confirmed_both_ways=link.confirmed_both_ways,
        )
        for link in db.scalars(select(Link))
        if link.a_device_id in visible_ids and link.b_device_id in visible_ids
    ]

    return TopologyGraph(
        layer=layer,
        nodes=nodes,
        edges=edges,
        unplaced_count=sum(1 for n in nodes if not n.placed),
    )


def _interface_counts(db: Session) -> dict[int, int]:
    counts: dict[int, int] = {}
    for interface in db.scalars(select(Interface)):
        counts[interface.device_id] = counts.get(interface.device_id, 0) + 1
    return counts


def _auto_layout(devices: list[Device]) -> dict[int, tuple[float, float]]:
    """種別を段にして並べる初期配置。人が動かすまでの仮置き。"""
    tiers: dict[int, list[Device]] = {}
    for device in devices:
        tier = TIER_ORDER.get(device.device_type, 7)
        tiers.setdefault(tier, []).append(device)

    positions: dict[int, tuple[float, float]] = {}
    for tier, members in sorted(tiers.items()):
        members.sort(key=lambda d: d.hostname)
        # 段の中央に寄せて並べる
        offset = -(len(members) - 1) / 2
        for index, device in enumerate(members):
            positions[device.id] = ((offset + index) * _X_SPACING, tier * _Y_SPACING)
    return positions


def save_positions(db: Session, layer: str, positions: list[tuple[int, float, float]]) -> int:
    existing = {
        node.device_id: node
        for node in db.scalars(select(TopologyNode).where(TopologyNode.layer == layer))
    }

    saved = 0
    for device_id, x, y in positions:
        node = existing.get(device_id)
        if node is None:
            node = TopologyNode(device_id=device_id, layer=layer)
            db.add(node)
        node.x = x
        node.y = y
        node.pinned = True
        saved += 1

    db.commit()
    return saved


def reset_positions(db: Session, layer: str) -> int:
    """保存済みの配置を消して、自動レイアウトに戻す。"""
    nodes = list(db.scalars(select(TopologyNode).where(TopologyNode.layer == layer)))
    for node in nodes:
        db.delete(node)
    db.commit()
    return len(nodes)


@dataclass
class TopologyStats:
    device_count: int
    link_count: int
    discovered_links: int
    manual_links: int
    confirmed_links: int
    isolated_devices: list[str]  # 1本も線が繋がっていない機器


def get_stats(db: Session, layer: str = DEFAULT_LAYER) -> TopologyStats:
    graph = get_graph(db, layer)
    connected = {e.source for e in graph.edges} | {e.target for e in graph.edges}
    return TopologyStats(
        device_count=len(graph.nodes),
        link_count=len(graph.edges),
        discovered_links=sum(1 for e in graph.edges if e.origin is LinkOrigin.DISCOVERED),
        manual_links=sum(1 for e in graph.edges if e.origin is LinkOrigin.MANUAL),
        confirmed_links=sum(1 for e in graph.edges if e.confirmed_both_ways),
        isolated_devices=sorted(n.hostname for n in graph.nodes if n.device_id not in connected),
    )


def suggested_canvas_size(node_count: int) -> tuple[int, int]:
    """自動配置したときにだいたい収まる大きさ。画面側の初期表示に使う。"""
    columns = max(1, ceil(node_count**0.5))
    return int(columns * _X_SPACING + 200), int(len(TIER_ORDER) * _Y_SPACING + 200)
