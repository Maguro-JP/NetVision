"""論理構成図（トンネル）を組み立てる。

物理構成図は「ケーブルがどう繋がっているか」。論理構成図は「その上で通信が
どう流れるか」。拠点から本社まで物理では支社・センターを何段も経由していても、
論理では GRE / IPsec でセンターと直結していることがある。

障害の切り分けで効くのは、たいていこのズレの方である。
物理を見て「途中の機器は関係ない」と判断すると外す。

宛先の解決は、Config に書かれた IP を台帳の管理IP・インターフェースIP と
突き合わせるだけにしてある。当たらなければ当たらないままにする。
それらしい機器を当てはめると、実際には張られていない線が図に出る。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceTunnel
from app.models.topology import Interface, LinkOrigin
from app.services.lifecycle import evaluate
from app.services.topology import TopologyEdgeView, TopologyGraph, TopologyNodeView

LOGICAL_LAYER = "logical"


@dataclass
class UnresolvedTunnel:
    """相手が特定できなかったトンネル。

    黙って捨てると「トンネルが無い」と読まれる。台帳に無い相手（他社側の
    装置やクラウド）に張っていることは普通にあるので、件数として必ず出す。
    """

    device: str
    name: str
    destination: str | None
    kind: str | None


@dataclass
class LogicalGraph:
    graph: TopologyGraph
    unresolved: list[UnresolvedTunnel] = field(default_factory=list)


def _address_index(db: Session) -> dict[str, int]:
    """IP から機器を引く表。管理IPとインターフェースIPの両方を入れる。

    管理IPは後から読んだ機器で上書きする。デモで FW とセンターが同じ
    アドレスを持ってしまったとき、先に入った FW にトンネルが全部吸われる
    と論理図が壊れる。インターフェースIPは setdefault のまま（管理IPを
    勝手に奪わない）。
    """
    index: dict[str, int] = {}
    for device in db.scalars(select(Device)):
        if device.mgmt_ip:
            index[device.mgmt_ip.split("/")[0]] = device.id
    for interface in db.scalars(select(Interface)):
        if interface.ip_address:
            index.setdefault(interface.ip_address.split("/")[0], interface.device_id)
    return index


def build(
    db: Session, *, include_retired: bool = False, orientation: str = "vertical"
) -> LogicalGraph:
    devices = list(db.scalars(select(Device).options(selectinload(Device.site))))
    if not include_retired:
        devices = [d for d in devices if d.status is not DeviceStatus.RETIRED]
    by_id = {d.id: d for d in devices}

    index = _address_index(db)
    tunnels = list(db.scalars(select(DeviceTunnel)))

    edges: list[TopologyEdgeView] = []
    unresolved: list[UnresolvedTunnel] = []
    seen: set[tuple[int, int, str]] = set()

    for tunnel in tunnels:
        if tunnel.device_id not in by_id:
            continue
        target = index.get((tunnel.destination or "").split("/")[0]) if tunnel.destination else None
        if target is None or target not in by_id or target == tunnel.device_id:
            unresolved.append(
                UnresolvedTunnel(
                    device=by_id[tunnel.device_id].hostname,
                    name=tunnel.name,
                    destination=tunnel.destination,
                    kind=tunnel.kind,
                )
            )
            continue

        # 両端から同じトンネルが出てくる（相互に張ってある）。1本にまとめる
        key = (min(tunnel.device_id, target), max(tunnel.device_id, target), tunnel.name)
        if key in seen:
            continue
        seen.add(key)

        edges.append(
            TopologyEdgeView(
                id=tunnel.id,
                source=tunnel.device_id,
                target=target,
                source_interface=tunnel.name,
                target_interface="",
                origin=LinkOrigin.DISCOVERED,
                protocol=tunnel.kind or "tunnel",
                label=tunnel.description or (tunnel.kind or "tunnel"),
                # 片側の設定しか見ていない。両側で確認できたことにはしない
                confirmed_both_ways=False,
                vrf=tunnel.vrf,
            )
        )

    # 論理図に出すのは、トンネルが張られている機器だけ。
    # 全機器を並べると、線の無い箱ばかりで何も読めない
    involved = {e.source for e in edges} | {e.target for e in edges}
    shown = [d for d in devices if d.id in involved]
    nodes = [
        TopologyNodeView(
            device_id=device.id,
            hostname=device.hostname,
            vendor=device.vendor,
            model=device.model,
            device_type=str(device.device_type),
            status=str(device.status),
            site_id=device.site_id,
            site_name=device.site.name if device.site else None,
            mgmt_ip=device.mgmt_ip,
            severity=evaluate(device).overall_severity,
            interface_count=0,
            x=0.0,
            y=0.0,
            placed=False,
        )
        for device in shown
    ]

    return LogicalGraph(
        graph=TopologyGraph(layer=LOGICAL_LAYER, nodes=nodes, edges=edges),
        unresolved=unresolved,
    )
