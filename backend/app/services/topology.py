"""トポロジー図の組み立て。

自動推定した結線を土台に、人が配置を直して図を仕上げる前提で作っている。

見やすい構成図の定石と、この製品での最適（docs/topology-samples）:

- 本命は拠点で絞った物理図。形は「拠点ツリー＋対向の列」
- 上（または左）が上位。センターは真上。配下の拠点は下ではなく右の列
- 段は結線のホップで決める。種別だけで横一列にしない。空の Core/Dist 段も作らない
- 同じ段は揃える。冗長の2台は同じ段に横並び（Dual Tree）。ホップの木に積まない
- 線は縦横だけ。箱の辺から出し、束は等間隔。交差は段内の並びとポート順で潰す
- 放射状・力学モデル・フルメッシュの描画は使わない
- 物理と論理は別の図（レイヤ）

自動配置は結線の上下とポート順で木を組む。種別の段だけに並べると、
同じ種別の上位・下位が横一列になり、図として正しくない。
人が動かして保存した位置は意思として残す。見るだけで戻したりしない。
「配置を整える」はマス目・揃い・交差の削減。消して初期配置に戻すのはリセット。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceType, DeviceVlan, DeviceVrf, Vlan
from app.models.topology import (
    Interface,
    Link,
    LinkOrigin,
    TopologyGroup,
    TopologyGroupMember,
    TopologyNode,
)
from app.services.configparse import expand, normalize, vlanlist
from app.services.lifecycle import Severity, evaluate

DEFAULT_LAYER = "physical"

# 全体図を描くとブラウザが固まる台数。これ以上は拠点か名前で絞ってもらう
UNSCOPED_GRAPH_LIMIT = 80

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

# 自動配置の間隔。機器の箱（190x78）に加えて、ポート名を置く余白を見込む。
# ここが詰まっていると、自動生成した図でラベルの置き場所が無くなる
_X_SPACING = 300.0
_Y_SPACING = 200.0

# 絞り込んだときの並べ直しで、1段に並べる上限と折り返した段の間隔
_WRAP = 6
_WRAP_SPACING = 130.0


def _layout_of(site_id: int | None):
    """この図の配置だけを取る。全体図は site_id が空の行。"""
    if site_id is None:
        return TopologyNode.site_id.is_(None)
    return TopologyNode.site_id == site_id


def _saved_positions(db: Session, layer: str, site_id: int | None) -> dict[int, TopologyNode]:
    return {
        node.device_id: node
        for node in db.scalars(
            select(TopologyNode).where(TopologyNode.layer == layer, _layout_of(site_id))
        )
    }


@dataclass
class TopologyNodeView:
    device_id: int
    hostname: str
    vendor: str
    model: str
    device_type: str
    status: str
    site_id: int | None
    site_name: str | None
    mgmt_ip: str | None
    severity: Severity
    interface_count: int
    x: float
    y: float
    placed: bool  # 保存済みの位置か、自動配置か
    # この機器に載っている VLAN。定義されているものと、トランクで通しているものの合計
    vlans: list[int] = field(default_factory=list)
    vrfs: list[str] = field(default_factory=list)  # この機器に切られている仮想ルータ
    # この機器で図に出てくるポートの種類（GigabitEthernet など）。
    # 線のラベルを番号だけにしても、ここを見れば何のポートか分かるようにするため
    port_kinds: list[str] = field(default_factory=list)
    # 絞り込みの外にいて、対向として足しただけの機器
    outside: bool = False
    # 同じ拠点にいて、代表1台にまとめた分の台数
    hidden_peers: int = 0
    # キャリア網の段。backbone=コア網 / metro=メトロ / access=アクセス
    plane: str = "access"


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
    # この線が実際に運んでいる VLAN。両端のポート設定を突き合わせて出す
    vlans: list[int] = field(default_factory=list)
    # 片側にしか無い VLAN。トランクの許可漏れはここに出る
    vlan_mismatch: list[int] = field(default_factory=list)
    native_vlan: int | None = None
    native_mismatch: bool = False  # 両端でネイティブ VLAN が食い違っている
    vrf: str | None = None
    # 人が指定した引き出し位置。空なら図の側で位置関係から決める
    source_side: str | None = None
    target_side: str | None = None


@dataclass
class TopologyGroupView:
    id: int
    name: str
    plane: str
    color: str
    device_ids: list[int]


@dataclass
class TopologyGraph:
    layer: str
    nodes: list[TopologyNodeView] = field(default_factory=list)
    edges: list[TopologyEdgeView] = field(default_factory=list)
    unplaced_count: int = 0
    groups: list[TopologyGroupView] = field(default_factory=list)


def matching_device_ids(
    db: Session, *, site_id: int | None = None, q: str | None = None
) -> set[int] | None:
    """絞り込みに当たる機器だけを返す。対向は含めない。

    図の主役（自拠点）と、線の先の簡易表示（対向）を分けるために使う。
    配置の保存先は topology_nodes.site_id なので、対向を図に出しても
    相手の拠点の行は触らない。

    絞り込みが無いときは None を返す（＝全部）。
    """
    keyword = (q or "").strip()
    if site_id is None and not keyword:
        return None

    stmt = select(Device.id)
    if site_id is not None:
        stmt = stmt.where(Device.site_id == site_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            Device.hostname.ilike(like) | Device.mgmt_ip.ilike(like),
        )
    return set(db.scalars(stmt))


def scoped_device_ids(
    db: Session, *, site_id: int | None = None, q: str | None = None
) -> set[int] | None:
    """図に要る機器だけを先に絞り込む。

    拠点を選んで見るとき、必要なのはその拠点の機器と、そこから線が
    出ている相手だけ。それ以外まで読み込むと、全国規模では図を1つ出す
    のに何分もかかる（16万台では返ってこなかった）。

    絞り込みが無いときは None を返す（＝全部）。
    """
    inside = matching_device_ids(db, site_id=site_id, q=q)
    if inside is None:
        return None
    if not inside:
        return inside

    # 対向（直接つながっている相手）まで。ここが無いと線が1本も出ない図になる
    neighbours: set[int] = set()
    for a, b in db.execute(
        select(Link.a_device_id, Link.b_device_id).where(
            Link.a_device_id.in_(inside) | Link.b_device_id.in_(inside)
        )
    ):
        neighbours.add(a)
        neighbours.add(b)
    return inside | neighbours


def unscoped_graph_too_wide(db: Session, *, include_retired: bool = False) -> bool:
    """絞らずに全体を出すと固まる規模か。"""
    stmt = select(func.count()).select_from(Device)
    if not include_retired:
        stmt = stmt.where(Device.status != DeviceStatus.RETIRED)
    return (db.scalar(stmt) or 0) > UNSCOPED_GRAPH_LIMIT


def get_graph(
    db: Session,
    layer: str = DEFAULT_LAYER,
    *,
    include_retired: bool = False,
    orientation: str = "vertical",
    only: set[int] | None = None,
    site_id: int | None = None,
) -> TopologyGraph:
    if layer == "logical":
        # 論理図はトンネルから組み立てる。物理の結線とは別の話なので、
        # 同じ関数の中で分岐させて、呼び出し側（画面・書き出し）は同じままにする
        from app.services import topology_logical

        graph = topology_logical.build(
            db, include_retired=include_retired, orientation=orientation
        ).graph
        graph = _apply_saved_positions(db, graph, orientation=orientation, site_id=site_id)
        # 拠点で絞ったときは、その拠点の機器に加え、scoped_device_ids が
        # 拾った対向（センター等）に張られたトンネルも残す。
        # ここを拠点の site_id だけで切ると、本社を選んだときに論理が空になる。
        if only is not None:
            edges = [e for e in graph.edges if e.source in only or e.target in only]
            graph = _shrink_to(graph, edges)
            if graph.nodes:
                _compact(
                    graph.nodes, graph.edges, rank=hierarchy_rank(graph), orientation=orientation
                )
                graph.unplaced_count = sum(1 for n in graph.nodes if not n.placed)
        if graph.nodes:
            apply_planes(graph.nodes, hierarchy_rank(graph))
        return graph

    device_query = select(Device).options(selectinload(Device.contracts), selectinload(Device.site))
    if only is not None:
        # 絞り込みが決まっているなら、その分だけ読む（scoped_device_ids を参照）
        device_query = device_query.where(Device.id.in_(only))
    devices = list(db.scalars(device_query))
    if not include_retired:
        devices = [d for d in devices if d.status is not DeviceStatus.RETIRED]

    visible_ids = {d.id for d in devices}

    positions = _saved_positions(db, layer, site_id)
    interface_counts = _interface_counts(db)
    interface_query = select(Interface)
    if only is not None:
        interface_query = interface_query.where(Interface.device_id.in_(only))
    interfaces = list(db.scalars(interface_query))
    defined = _defined_vlans(db)
    vlans_by_device = _vlans_by_device(interfaces, defined)
    vrfs_by_device = _vrfs_by_device(db)
    ports = {(i.device_id, normalize(i.name)): i for i in interfaces}

    link_query = select(Link)
    if only is not None:
        link_query = link_query.where(Link.a_device_id.in_(only) & Link.b_device_id.in_(only))
    links = [
        link
        for link in db.scalars(link_query)
        if link.a_device_id in visible_ids and link.b_device_id in visible_ids
    ]
    # 図に出てくるポートの種類は、線が繋がっているポートだけを見る。
    # 使っていないポートまで並べると、機器の箱が読めなくなる
    port_kinds = _port_kinds(links)

    nodes: list[TopologyNodeView] = []
    for device in devices:
        saved = positions.get(device.id)
        x, y = (saved.x, saved.y) if saved else (0.0, 0.0)
        nodes.append(
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
                interface_count=interface_counts.get(device.id, 0),
                x=x,
                y=y,
                placed=saved is not None,
                vlans=vlans_by_device.get(device.id, []),
                vrfs=vrfs_by_device.get(device.id, []),
                port_kinds=port_kinds.get(device.id, []),
            )
        )

    edges = [_edge_view(link, ports, defined) for link in links]
    graph = TopologyGraph(
        layer=layer,
        nodes=nodes,
        edges=edges,
        unplaced_count=sum(1 for n in nodes if not n.placed),
    )

    # L2 / L3 は物理の結線を土台に、見る内容だけを差し替える。
    # 配置はレイヤ別に残るので、同じ線でも並べ方は別にできる。
    if layer == "l2":
        graph = _as_l2(graph)
    elif layer == "l3":
        graph = _as_l3(graph, ports)

    # 未配置だけ、結線に沿った木で置く。保存済みは意思なので触らない
    if graph.nodes:
        _compact(graph.nodes, graph.edges, rank=hierarchy_rank(graph), orientation=orientation)
        apply_planes(graph.nodes, hierarchy_rank(graph))
        graph.unplaced_count = sum(1 for n in graph.nodes if not n.placed)
    return graph


#: L3 図に出す機器の種別。スイッチ同士のトランクは L2 側の話。
_L3_TYPES = frozenset(
    {
        str(DeviceType.ROUTER),
        str(DeviceType.FIREWALL),
        str(DeviceType.LOAD_BALANCER),
    }
)


def _shrink_to(graph: TopologyGraph, edges: list[TopologyEdgeView]) -> TopologyGraph:
    """残した線に載っている機器だけを残す。線の無い箱は図を読めなくする。"""
    involved = {e.source for e in edges} | {e.target for e in edges}
    nodes = [n for n in graph.nodes if n.device_id in involved]
    return TopologyGraph(
        layer=graph.layer,
        nodes=nodes,
        edges=edges,
        unplaced_count=sum(1 for n in nodes if not n.placed),
        groups=graph.groups,
    )


def _as_l2(graph: TopologyGraph) -> TopologyGraph:
    """VLAN が載っている結線だけを残す。

    物理図の WAN 線（ルータ同士で VLAN が無い）は出さない。スイッチ間の
    トランクやアクセスが残るので、「どこまで同じ VLAN が伸びているか」が読める。

    人が手で引いた線（manual）は、VLAN がまだ付いていなくても残す。
    図の上で追加した直後に消えると、反映されていないように見える。
    """
    edges = [
        edge
        for edge in graph.edges
        if edge.origin is LinkOrigin.MANUAL
        or edge.vlans
        or edge.vlan_mismatch
        or edge.native_vlan is not None
    ]
    return _shrink_to(graph, edges)


def _as_l3(graph: TopologyGraph, ports: dict[tuple[int, str], Interface]) -> TopologyGraph:
    """セグメント（L3）の結線だけを残す。

    - ルータ / FW / LB どうしの物理結線
    - 両端のポートに IP が付いている結線（点対点のルート）
    - VRF が付いている結線
    - 人が手で引いた線（manual）

    スイッチ間の VLAN トランクは L2 側。混ぜると何の図か分からなくなる。
    手引きの線は、追加した直後に消えないよう残す。
    """
    by_id = {n.device_id: n for n in graph.nodes}
    edges: list[TopologyEdgeView] = []
    for edge in graph.edges:
        if edge.origin is LinkOrigin.MANUAL:
            edges.append(edge)
            continue
        a = by_id.get(edge.source)
        b = by_id.get(edge.target)
        if a is None or b is None:
            continue
        a_port = ports.get((edge.source, normalize(edge.source_interface)))
        b_port = ports.get((edge.target, normalize(edge.target_interface)))
        a_ip = bool(a_port and a_port.ip_address)
        b_ip = bool(b_port and b_port.ip_address)
        routed = a.device_type in _L3_TYPES and b.device_type in _L3_TYPES
        if routed or (a_ip and b_ip) or edge.vrf:
            edges.append(edge)
    return _shrink_to(graph, edges)


def _apply_saved_positions(
    db: Session,
    graph: TopologyGraph,
    *,
    orientation: str = "vertical",
    site_id: int | None = None,
) -> TopologyGraph:
    """人が動かした位置を反映する。図の種類が違っても、直した配置は残す。

    取るのはいま見ている拠点の図。別拠点で置いた座標は使わない。
    """
    saved = _saved_positions(db, graph.layer, site_id)
    for node in graph.nodes:
        position = saved.get(node.device_id)
        if position is not None:
            node.x, node.y, node.placed = position.x, position.y, True
    if graph.nodes:
        _compact(graph.nodes, graph.edges, rank=hierarchy_rank(graph), orientation=orientation)
    graph.unplaced_count = sum(1 for n in graph.nodes if not n.placed)
    return graph


SCOPE_ALL = "all"
SCOPE_SITE = "site"
SCOPE_PEERS = "peers"


def scope_graph(
    graph: TopologyGraph,
    *,
    site_id: int | None = None,
    q: str | None = None,
    scope: str = SCOPE_PEERS,
    orientation: str = "vertical",
    rank: dict[int, int] | None = None,
) -> TopologyGraph:
    """見たい範囲だけに絞る。

    拠点が数千・数万になると全体図は誰も見ない。実際に見るのは
    「自分の拠点」と「その上位（対向）まで」で、そこを軽く出せないと
    道具として使えない。既定は対向まで（`peers`）。

    対向まで含めるのは、拠点だけに絞ると線が1本も出ず「どこにも繋がって
    いない」図になってしまうため。
    """
    keyword = (q or "").strip().lower()
    if site_id is None and not keyword:
        return graph

    def matches(node: TopologyNodeView) -> bool:
        if site_id is not None and node.site_id != site_id:
            return False
        if keyword:
            haystack = f"{node.hostname} {node.site_name or ''} {node.mgmt_ip or ''}".lower()
            if keyword not in haystack:
                return False
        return True

    inside = {node.device_id for node in graph.nodes if matches(node)}
    by_id = {node.device_id: node for node in graph.nodes}
    keep = set(inside)
    hidden: dict[int, int] = {}

    if scope != SCOPE_SITE:
        # 対向は「向こう側の入口1台」だけにする。
        # 東京を見たいのに群馬まで出てきては、肝心の東京が読めない。
        # 相手の拠点ごとに、こちらと一番多く繋がっている1台を代表にする
        links_to_inside: dict[int, int] = {}
        for edge in graph.edges:
            for near, far in ((edge.source, edge.target), (edge.target, edge.source)):
                if near in inside and far not in inside:
                    links_to_inside[far] = links_to_inside.get(far, 0) + 1

        groups: dict[object, list[int]] = {}
        for device_id in links_to_inside:
            node = by_id[device_id]
            # 拠点が入っていない機器は、まとめずに1台ずつ扱う
            groups.setdefault(node.site_id if node.site_id is not None else device_id, []).append(
                device_id
            )

        for members in groups.values():
            members.sort(key=lambda i: (-links_to_inside[i], by_id[i].hostname))
            keep.add(members[0])
            # 隠した分は数として出す。黙って消すと「そこには1台しかない」と読まれる
            if len(members) > 1:
                hidden[members[0]] = len(members) - 1

    nodes = [node for node in graph.nodes if node.device_id in keep]
    # 上下関係は網全体で見る。切り出した中だけで決めると、その範囲で
    # たまたま真ん中にいる機器が最上位になってしまう
    _compact(
        nodes,
        [e for e in graph.edges if e.source in keep and e.target in keep],
        rank=rank if rank is not None else hierarchy_rank(graph),
        inside=inside,
        orientation=orientation,
        world=graph,
    )
    for node in nodes:
        # 対向として足しただけの機器は、図の上で区別できるようにする
        node.outside = node.device_id not in inside
        node.hidden_peers = hidden.get(node.device_id, 0)

    used_rank = rank if rank is not None else hierarchy_rank(graph)
    apply_planes(nodes, used_rank)
    return TopologyGraph(
        layer=graph.layer,
        nodes=nodes,
        # 対向どうしを結ぶ線は出さない。絞り込んだ範囲の外の話で、
        # そこまで描くと「隣の家の中」まで図に出てくる
        edges=[
            e
            for e in graph.edges
            if e.source in keep and e.target in keep and (e.source in inside or e.target in inside)
        ],
        unplaced_count=graph.unplaced_count,
        groups=graph.groups,
    )


def _adjacency(graph) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {n.device_id: [] for n in graph.nodes}
    for edge in graph.edges:
        if edge.source in out and edge.target in out:
            out[edge.source].append(edge.target)
            out[edge.target].append(edge.source)
    return out


def _far(start: int, adjacency: dict[int, list[int]]) -> tuple[int, dict[int, int]]:
    """`start` から一番遠い機器と、各機器までの距離。"""
    distance = {start: 0}
    queue = [start]
    last = start
    while queue:
        current = queue.pop(0)
        last = current
        for neighbour in adjacency.get(current, []):
            if neighbour not in distance:
                distance[neighbour] = distance[current] + 1
                queue.append(neighbour)
    return last, distance


def network_rank(db: Session) -> dict[int, int]:
    """網全体で見た上下関係。結線だけを読むので、台数が増えても軽い。

    上下は網全体で決めないと意味がない。1拠点分に絞ったあとで求めると、
    その範囲でたまたま真ん中にいる機器が最上位になってしまう。
    かといって全機器を読み直すのは重いので、必要な結線だけを読む。
    """
    adjacency: dict[int, set[int]] = defaultdict(set)
    for a, b in db.execute(select(Link.a_device_id, Link.b_device_id)):
        adjacency[a].add(b)
        adjacency[b].add(a)
    return _rank_from_adjacency(adjacency)


def hierarchy_rank(graph) -> dict[int, int]:
    """網全体の「上位からの遠さ」。小さいほど上位。

    繋がっている本数では上下は決まらない。支社ルータは配下の拠点が多いぶん
    本数が多くなり、センターより上に見えてしまう。

    代わりに網の中心を求める。端から端（一番長い経路）の真ん中にいるのは
    センター側なので、そこからのホップ数を上下の目安にする。
    木構造の網ではこれがよく合う。
    """
    return _rank_from_adjacency(_adjacency(graph))


def _rank_from_adjacency(adjacency: dict[int, set[int]]) -> dict[int, int]:
    """隣接から上下を求める。中心（一番長い経路の真ん中）からのホップ数。"""
    if not adjacency:
        return {}

    ranks: dict[int, int] = {}
    remaining = set(adjacency)
    while remaining:
        # 島ごとに求める。繋がっていない機器同士の上下は比べようがない
        seed = min(remaining)
        end_a, _ = _far(seed, adjacency)
        end_b, from_a = _far(end_a, adjacency)
        _, from_b = _far(end_b, adjacency)
        # 端から端への経路の真ん中にある機器を中心とする
        component = [device_id for device_id in from_a if device_id in remaining]
        centre = min(
            component,
            key=lambda device_id: (
                max(from_a.get(device_id, 0), from_b.get(device_id, 0)),
                device_id,
            ),
        )
        _, distance = _far(centre, adjacency)
        for device_id in component:
            ranks[device_id] = distance.get(device_id, 0)
            remaining.discard(device_id)
    return ranks


PLANE_BACKBONE = "backbone"
PLANE_METRO = "metro"
PLANE_ACCESS = "access"
PLANE_COLORS = {
    PLANE_BACKBONE: "#2f4d6a",
    PLANE_METRO: "#4a7a58",
    PLANE_ACCESS: "#8a6a3a",
    "custom": "#5a7a9a",
}
PLANE_NAMES = {
    PLANE_BACKBONE: "コア網",
    PLANE_METRO: "メトロ／集約",
    PLANE_ACCESS: "アクセス",
    "custom": "任意",
}


def plane_of(rank: int, deepest: int) -> str:
    """ホップからキャリア網の段を決める。

    コア網は中心、アクセスは端。中間はメトロ（集約）。
    1段しか無い網は全部コアにしない。端の営業所までコアに見える。
    """
    if deepest <= 0:
        return PLANE_ACCESS
    if deepest == 1:
        return PLANE_BACKBONE if rank <= 0 else PLANE_ACCESS
    ratio = rank / deepest
    if ratio <= 0.34:
        return PLANE_BACKBONE
    if ratio <= 0.67:
        return PLANE_METRO
    return PLANE_ACCESS


def apply_planes(nodes: list[TopologyNodeView], rank: dict[int, int]) -> None:
    """見える機器に、コア／メトロ／アクセスの段を付ける。"""
    if not nodes:
        return
    deepest = max((rank.get(node.device_id, 0) for node in nodes), default=0)
    for node in nodes:
        node.plane = plane_of(rank.get(node.device_id, deepest), deepest)


def attach_groups(db: Session, graph: TopologyGraph) -> TopologyGraph:
    """今見えている機器が属する枠だけを図に載せる。"""
    visible = {node.device_id for node in graph.nodes}
    rows = list(
        db.scalars(
            select(TopologyGroup)
            .options(selectinload(TopologyGroup.members))
            .where(TopologyGroup.layer == graph.layer)
        )
    )
    groups: list[TopologyGroupView] = []
    for row in rows:
        ids = [member.device_id for member in row.members if member.device_id in visible]
        if not ids:
            continue
        groups.append(
            TopologyGroupView(
                id=row.id,
                name=row.name,
                plane=row.plane,
                color=row.color,
                device_ids=ids,
            )
        )
    graph.groups = groups
    return graph


def create_group(
    db: Session,
    layer: str,
    name: str,
    device_ids: list[int],
    *,
    plane: str = "custom",
    color: str | None = None,
) -> TopologyGroupView:
    """選んだ機器を枠で囲む。"""
    wanted = list(dict.fromkeys(device_ids))
    if len(wanted) < 1:
        raise ValueError("枠に入れる機器が無い")
    hue = color or PLANE_COLORS.get(plane, PLANE_COLORS["custom"])
    row = TopologyGroup(layer=layer, name=name.strip() or "枠", plane=plane, color=hue)
    db.add(row)
    db.flush()
    for device_id in wanted:
        db.add(TopologyGroupMember(group_id=row.id, device_id=device_id))
    db.commit()
    db.refresh(row)
    return TopologyGroupView(
        id=row.id,
        name=row.name,
        plane=row.plane,
        color=row.color,
        device_ids=wanted,
    )


def update_group(
    db: Session,
    group_id: int,
    *,
    name: str | None = None,
    plane: str | None = None,
    color: str | None = None,
    device_ids: list[int] | None = None,
) -> TopologyGroupView | None:
    row = db.get(TopologyGroup, group_id)
    if row is None:
        return None
    if name is not None:
        row.name = name.strip() or row.name
    if plane is not None:
        row.plane = plane
        if color is None:
            row.color = PLANE_COLORS.get(plane, row.color)
    if color is not None:
        row.color = color
    if device_ids is not None:
        wanted = list(dict.fromkeys(device_ids))
        row.members.clear()
        db.flush()
        for device_id in wanted:
            db.add(TopologyGroupMember(group_id=row.id, device_id=device_id))
    db.commit()
    db.refresh(row)
    return TopologyGroupView(
        id=row.id,
        name=row.name,
        plane=row.plane,
        color=row.color,
        device_ids=[member.device_id for member in row.members],
    )


def delete_group(db: Session, group_id: int) -> bool:
    row = db.get(TopologyGroup, group_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _grouped_device_ids(db: Session, layer: str) -> set[int]:
    rows = list(
        db.scalars(
            select(TopologyGroup)
            .options(selectinload(TopologyGroup.members))
            .where(TopologyGroup.layer == layer)
        )
    )
    taken: set[int] = set()
    for row in rows:
        taken.update(member.device_id for member in row.members)
    return taken


def groups_from_sites(db: Session, layer: str, device_ids: list[int]) -> list[TopologyGroupView]:
    """見えている機器を拠点ごとに枠へ入れる。既にある枠は触らない。"""
    if not device_ids:
        return []
    devices = list(
        db.scalars(
            select(Device).where(Device.id.in_(device_ids)).options(selectinload(Device.site))
        )
    )
    taken = _grouped_device_ids(db, layer)
    by_site: dict[int | None, list[Device]] = {}
    for device in devices:
        if device.id in taken:
            continue
        by_site.setdefault(device.site_id, []).append(device)
    created: list[TopologyGroupView] = []
    for _site_id, members in by_site.items():
        if len(members) < 1:
            continue
        name = members[0].site.name if members[0].site else "拠点未登録"
        created.append(
            create_group(
                db,
                layer,
                name,
                [device.id for device in members],
                plane="custom",
                color=PLANE_COLORS["custom"],
            )
        )
    return created


def groups_from_planes(db: Session, layer: str, device_ids: list[int]) -> list[TopologyGroupView]:
    """見えている機器をコア／メトロ／アクセスの段ごとに枠へ入れる。既にある枠は触らない。"""
    if not device_ids:
        return []
    graph = get_graph(db, layer, only=set(device_ids))
    wanted = set(device_ids)
    taken = _grouped_device_ids(db, layer)
    by_plane: dict[str, list[int]] = {}
    for node in graph.nodes:
        if node.device_id not in wanted or node.device_id in taken:
            continue
        by_plane.setdefault(node.plane or PLANE_ACCESS, []).append(node.device_id)
    created: list[TopologyGroupView] = []
    for plane, ids in by_plane.items():
        if not ids:
            continue
        created.append(
            create_group(
                db,
                layer,
                PLANE_NAMES.get(plane, plane),
                ids,
                plane=plane,
                color=PLANE_COLORS.get(plane, PLANE_COLORS["custom"]),
            )
        )
    return created


def _align_redundant_pairs(
    rank: dict[int, int], neighbours: dict[int, list[int]]
) -> dict[int, int]:
    """冗長の対を同じ段にする。

    網の中心からのホップだと、2台のコアの片方だけが中心になり、
    もう一台が一段下に落ちる。両方に同じ配下が2つ以上ある対は、同じ段。
    """
    adjusted = dict(rank)
    seen: set[tuple[int, int]] = set()
    for device_id, nbs in neighbours.items():
        nb_set = set(nbs)
        for other in nbs:
            if other == device_id:
                continue
            pair = (device_id, other) if device_id < other else (other, device_id)
            if pair in seen:
                continue
            seen.add(pair)
            if abs(adjusted.get(device_id, 0) - adjusted.get(other, 0)) > 1:
                continue
            common = nb_set & set(neighbours.get(other, []))
            common.discard(device_id)
            common.discard(other)
            if len(common) < 2:
                continue
            layer = min(adjusted.get(device_id, 0), adjusted.get(other, 0))
            adjusted[device_id] = layer
            adjusted[other] = layer
    return adjusted


def _compact(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView] | None = None,
    *,
    rank: dict[int, int] | None = None,
    inside: set[int] | None = None,
    orientation: str = "vertical",
    polish: bool = False,
    world: TopologyGraph | None = None,
) -> None:
    """結線の上下で段を決め、同じ段は横に並べる。

    初期配置（`polish=False`）は、未配置だけをポート順の木に置く。
    冗長の2台は同じ段。BFS の親子に積まない。
    人が動かした位置は意思なので、見るだけでは触らない。

    人が動かした配置の整頓は `_polish_layout` の仕事。こちらで木を組み直すと
    「初期配置が正解」になってしまう。
    """
    if not nodes:
        return

    owned = [n for n in nodes if inside is None or n.device_id in inside] or list(nodes)
    everything = {node.device_id: node for node in nodes}
    tree_nodes = owned
    keep = {node.device_id for node in tree_nodes}
    by_id = {node.device_id: node for node in tree_nodes}
    graph = TopologyGraph(
        layer="",
        nodes=tree_nodes,
        edges=[e for e in (edges or []) if e.source in keep and e.target in keep],
    )
    if rank is None:
        rank = hierarchy_rank(graph)

    neighbours = _adjacency(graph)
    rank = _align_redundant_pairs(rank, neighbours)
    port_between: dict[tuple[int, int], str] = {}
    for edge in graph.edges:
        port_between[(edge.source, edge.target)] = edge.source_interface
        port_between[(edge.target, edge.source)] = edge.target_interface

    horizontal = orientation == "horizontal"

    # 段はホップ（rank）。BFS の木の深さだと、同じ段の冗長対が親子になる
    depth: dict[int, int] = {}
    linked = [device_id for device_id in by_id if neighbours.get(device_id)]
    if linked:
        base = min(rank.get(device_id, 0) for device_id in linked)
        for device_id in by_id:
            if neighbours.get(device_id):
                depth[device_id] = rank.get(device_id, base) - base
            else:
                depth[device_id] = _tier_of(by_id[device_id])
    else:
        for device_id in by_id:
            depth[device_id] = _tier_of(by_id[device_id])

    def downstream(device_id: int) -> list[int]:
        return [
            other
            for other in neighbours.get(device_id, [])
            if other in by_id and depth.get(other, 0) > depth[device_id]
        ]

    children: dict[int, list[int]] = {}
    parents_of: dict[int, list[int]] = defaultdict(list)
    for device_id in by_id:
        ordered = _child_order(
            device_id,
            downstream(device_id),
            by_id,
            port_between,
            horizontal=horizontal,
            polish=polish,
        )
        children[device_id] = ordered
        for kid in ordered:
            parents_of[kid].append(device_id)

    exclusive: dict[int, list[int]] = {device_id: [] for device_id in by_id}
    for kid, parents in parents_of.items():
        parents.sort(
            key=lambda parent: (
                depth[parent],
                by_id[parent].hostname,
                port_key(port_between.get((parent, kid), "")),
            )
        )
        exclusive[parents[0]].append(kid)
    for parent, kids in exclusive.items():
        exclusive[parent] = _child_order(
            parent,
            kids,
            by_id,
            port_between,
            horizontal=horizontal,
            polish=polish,
        )

    roots = [
        device_id
        for device_id in sorted(by_id, key=lambda i: (depth[i], by_id[i].hostname))
        if not any(
            other in by_id and depth[other] < depth[device_id]
            for other in neighbours.get(device_id, [])
        )
    ]

    # 葉から順に横位置を決め、親は子の中央に置く。これで線が交差しない
    slot = 0.0
    across: dict[int, float] = {}

    def place(device_id: int) -> float:
        nonlocal slot
        kids = exclusive.get(device_id, [])
        if not kids:
            across[device_id] = slot
            slot += 1.0
            return across[device_id]
        spots = [place(kid) for kid in kids]
        across[device_id] = (spots[0] + spots[-1]) / 2
        return across[device_id]

    for root in roots:
        place(root)
        slot += 1.0  # 島の間を1つ空ける

    for device_id in by_id:
        if device_id not in across:
            across[device_id] = slot
            slot += 1.0

    # 冗長の対は、配下の中央へ寄せて同じ段に並べる
    for device_id, kids in children.items():
        spots = [across[kid] for kid in kids if kid in across]
        if spots:
            across[device_id] = sum(spots) / len(spots)
    by_depth: dict[int, list[int]] = defaultdict(list)
    for device_id, level in depth.items():
        by_depth[level].append(device_id)
    for group in by_depth.values():
        group.sort(key=lambda device_id: (across.get(device_id, 0.0), by_id[device_id].hostname))
        for index in range(1, len(group)):
            prev = group[index - 1]
            current = group[index]
            if across[current] - across[prev] < 1.0:
                across[current] = across[prev] + 1.0

    move = {node.device_id for node in owned if polish or not node.placed}
    for device_id, node in by_id.items():
        if device_id not in move:
            continue
        along = across.get(device_id, 0.0) * _X_SPACING
        down = depth.get(device_id, 0) * _Y_SPACING
        node.x, node.y = (down, along) if horizontal else (along, down)

    if inside:
        _place_peers(
            everything,
            TopologyGraph(layer="", nodes=nodes, edges=list(edges or [])),
            inside,
            rank,
            horizontal=horizontal,
            world=world,
        )


def _child_order(
    parent_id: int,
    candidates: list[int],
    by_id: dict[int, TopologyNodeView],
    port_between: dict[tuple[int, int], str],
    *,
    horizontal: bool,
    polish: bool,
) -> list[int]:
    """配下の左右の順。

    初期配置はポート番号順。実機の並びと線が交差しないことの両方に効く。
    最適化は、人が置いた左右を意思として残し、その間に未配置をポート順で入れる。
    """
    if not candidates:
        return []

    def by_port(device_id: int) -> tuple:
        return (
            port_key(port_between.get((parent_id, device_id), "")),
            by_id[device_id].hostname,
        )

    ideal = sorted(candidates, key=by_port)
    if not polish:
        return ideal

    placed = [device_id for device_id in candidates if by_id[device_id].placed]
    if not placed:
        return ideal

    def across(device_id: int) -> float:
        node = by_id[device_id]
        return node.y if horizontal else node.x

    intent = {device_id: index for index, device_id in enumerate(sorted(placed, key=across))}
    ideal_index = {device_id: index for index, device_id in enumerate(ideal)}

    def sort_key(device_id: int) -> tuple:
        if device_id in intent:
            return (intent[device_id], 0, 0)
        i = ideal_index[device_id]
        left = [intent[x] for x in ideal[:i] if x in intent]
        if left:
            return (left[-1], 1, i)
        right = [intent[x] for x in ideal[i + 1 :] if x in intent]
        if right:
            return (right[0] - 0.5, i, 0)
        return (0, i, 0)

    return sorted(candidates, key=sort_key)


# 機器の箱は、結線が多いほど横に広がる（画面側の widthFor と同じ規則）。
# 線を1本ずつ別の通り道に沿わせるための幅も要る。
# どちらも見込まずに対向を置くと、箱の右端が対向側へ食い込み、
# 線がいったん行き過ぎてから戻る形になって、その戻りが交差に見える。
_BOX_WIDTH = 190.0
_BOX_GROW = 55.0
_BOX_MAX = 620.0
_LANE_GAP = 16.0


def _box_width(links: int) -> float:
    """結線本数から、画面上の箱幅を出す（frontend の widthFor と同じ）。"""
    if links <= 3:
        return _BOX_WIDTH
    return min(_BOX_MAX, _BOX_WIDTH + (links - 3) * _BOX_GROW)


def _visual_left(stored_x: float, links: int) -> float:
    """保存座標（左上）から、画面に見える左端を出す。"""
    width = _box_width(links)
    shift = (width - _BOX_WIDTH) / 2
    return stored_x - shift


def _visual_right(stored_x: float, links: int) -> float:
    """保存座標（左上）から、画面に見える右端を出す。"""
    width = _box_width(links)
    shift = (width - _BOX_WIDTH) / 2
    return stored_x - shift + width


def _lane_room(links: int) -> float:
    """束ねた線を通す通り道の幅。箱の広がりは _visual_right で見込む。"""
    if links <= 0:
        return 0.0
    return links * _LANE_GAP


def _peer_kind(
    peer_id: int,
    inside: set[int],
    rank: dict[int, int],
    own_rank: int,
    world_adj: dict[int, list[int]],
    world_by_id: dict[int, TopologyNodeView],
) -> str:
    """対向を上・横・下のどれに置くか。

    ホップだけだと、見ている支社が網の中心になり、センターまで配下に落ちる。
    向こうの拠点で機器どうしが繋がっている対向はセンター側（上）と見る。
    """
    level = rank.get(peer_id, own_rank)
    if level < own_rank:
        return "up"
    peer = world_by_id.get(peer_id)
    peer_site = peer.site_id if peer is not None else None
    if peer_site is not None:
        for nb in world_adj.get(peer_id, []):
            if nb in inside:
                continue
            other = world_by_id.get(nb)
            if other is not None and other.site_id == peer_site:
                return "up"
    if level > own_rank:
        return "down"
    return "down"


def _place_peers(
    by_id: dict[int, TopologyNodeView],
    graph: TopologyGraph,
    inside: set[int],
    rank: dict[int, int],
    *,
    horizontal: bool,
    world: TopologyGraph | None = None,
) -> None:
    """対向（絞り込みの外）を、自拠点から見た向きに置き直す。

    見たいのは自拠点の機器なので、そこを図の主役に据える。

    - 上位は真上。どこから来ているかは1本の線で足りる
    - 同じ段の相手は横。支社どうしのような並びの関係
    - 配下の拠点は右へ縦に並べる。台数が増えても図が横に伸びない
    """
    if not inside:
        return

    own = [by_id[i] for i in inside if i in by_id]
    if not own:
        return
    link_count: dict[int, int] = defaultdict(int)
    for edge in graph.edges:
        if edge.source in inside:
            link_count[edge.source] += 1
        if edge.target in inside:
            link_count[edge.target] += 1

    def links_of(node: TopologyNodeView) -> int:
        return link_count.get(node.device_id, 0)

    left = min(_visual_left(node.x, links_of(node)) for node in own)
    right = max(_visual_right(node.x, links_of(node)) for node in own)
    top = min(node.y for node in own)
    middle = (left + right) / 2
    own_rank = min(rank.get(node.device_id, 0) for node in own)
    world_graph = world or graph
    world_adj = _adjacency(world_graph)
    world_by_id = {node.device_id: node for node in world_graph.nodes}

    # 対向がどの内側の機器につながっているか
    attached: dict[int, int] = {}
    for edge in graph.edges:
        if edge.source in inside and edge.target not in inside:
            attached.setdefault(edge.target, edge.source)
        elif edge.target in inside and edge.source not in inside:
            attached.setdefault(edge.source, edge.target)

    above: list[TopologyNodeView] = []
    beside: list[TopologyNodeView] = []
    below: list[TopologyNodeView] = []
    for device_id, node in by_id.items():
        if device_id in inside or device_id not in attached:
            continue
        if node.placed:
            # この拠点の図で置いた対向は意思。別拠点の座標は get_graph で載らない
            continue
        kind = _peer_kind(device_id, inside, rank, own_rank, world_adj, world_by_id)
        if kind == "up":
            above.append(node)
        elif kind == "side":
            beside.append(node)
        else:
            below.append(node)

    for group in (above, beside, below):
        group.sort(key=lambda node: node.hostname)

    for index, node in enumerate(above):
        offset = (index - (len(above) - 1) / 2) * _X_SPACING
        node.x, node.y = middle + offset, top - _Y_SPACING

    for index, node in enumerate(beside):
        node.x, node.y = right + (index + 1) * _X_SPACING * 1.6, top

    # 配下は右へ縦に並べる。斜め下に伸ばすと、台数が増えたとき紙に収まらない
    below_ids = {node.device_id for node in below}
    peer_links = 0
    for edge in graph.edges:
        if edge.source in inside and edge.target in below_ids:
            peer_links += 1
        elif edge.target in inside and edge.source in below_ids:
            peer_links += 1
    column = right + _X_SPACING * 1.6 + _lane_room(max(peer_links, len(below)))
    for index, node in enumerate(below):
        # 自拠点の側と重ならないよう、線を通す余白を見込んで離す
        node.x = column
        node.y = top + (index + 1) * (_Y_SPACING * 0.7)

    if horizontal:
        # 左右に並べる指定のときは、上下と左右を入れ替える
        for node in above + beside + below:
            node.x, node.y = node.y, node.x


def port_key(name: str) -> tuple:
    """ポート名を並べ替えるための鍵。数字は数として見る。

    文字列のまま並べると 1/0/10 が 1/0/2 より前に来て、図の並びが
    実機のポート番号と食い違う。
    """
    if not name:
        return (1, (), "")
    numbers = tuple(int(part) for part in re.findall(r"\d+", name))
    prefix = re.sub(r"[\d/:.\-]+", "", name).lower()
    return (0, (prefix, numbers), name)


def _tier_of(node: TopologyNodeView) -> int:
    try:
        return TIER_ORDER.get(DeviceType(node.device_type), 7)
    except ValueError:
        return 7


def _defined_vlans(db: Session) -> dict[int, set[int]]:
    """機器ごとに「定義されている」VLAN。`all` 許可のトランクを解くのにも使う。"""
    out: dict[int, set[int]] = {}
    for row in db.scalars(select(DeviceVlan)):
        out.setdefault(row.device_id, set()).add(row.vlan_id)
    return out


def port_vlans(interface: Interface, defined: set[int]) -> set[int]:
    """1ポートが運ぶ VLAN。アクセス・ネイティブ・トランク許可を合わせたもの。"""
    out = vlanlist.expand_column(interface.trunk_vlans, defined)
    for value in (interface.vlan, interface.native_vlan):
        if value is not None:
            out.add(value)
    return out


def _vlans_by_device(
    interfaces: list[Interface], defined: dict[int, set[int]]
) -> dict[int, list[int]]:
    """機器ごとの VLAN 一覧。図で「この VLAN がどこまで伸びているか」を見るのに使う。

    定義されているものだけでは足りない。トランクで通しているだけの VLAN も
    その機器を経由している以上、経路上に載っているためである。
    """
    out = {device_id: set(values) for device_id, values in defined.items()}
    for interface in interfaces:
        carried = port_vlans(interface, defined.get(interface.device_id, set()))
        if carried:
            out.setdefault(interface.device_id, set()).update(carried)
    return {device_id: sorted(values) for device_id, values in out.items()}


def _port_kinds(links: list[Link]) -> dict[int, list[str]]:
    """機器ごとに、図に出てくるポートの正式名を集める。

    線のラベルを番号だけにすると、それが何のポートか分からなくなる。
    色で表すこともできるが、色は凡例を探しに行かないと読めない。
    機器の箱に種類を書いておけば、視線を動かさずに済む。
    """
    out: dict[int, set[str]] = {}
    for link in links:
        for device_id, name in (
            (link.a_device_id, link.a_interface),
            (link.b_device_id, link.b_interface),
        ):
            kind = _kind_of(name)
            if kind:
                out.setdefault(device_id, set()).add(kind)
    return {device_id: sorted(kinds) for device_id, kinds in out.items()}


def _kind_of(name: str) -> str | None:
    """`GigabitEthernet1/0/24` → `GigabitEthernet`。番号を落とした部分。"""
    if not name:
        return None
    expanded = expand(name)
    match = re.match(r"^([A-Za-z][A-Za-z\-]*)", expanded)
    # Juniper の `ge-0/0/1` は接頭辞にハイフンが付くので落とす
    return match.group(1).rstrip("-") if match else None


def _vrfs_by_device(db: Session) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for row in db.scalars(select(DeviceVrf).order_by(DeviceVrf.name)):
        out.setdefault(row.device_id, []).append(row.name)
    return out


def _edge_view(
    link: Link, ports: dict[tuple[int, str], Interface], defined: dict[int, set[int]]
) -> TopologyEdgeView:
    """結線1本分。両端のポート設定から、その線が何を運んでいるかを出す。

    片側でしか許可していない VLAN は `vlan_mismatch` に出す。トランクの
    許可漏れは「繋がっているのに通らない」の典型で、図の上で気づけると早い。
    """
    a = ports.get((link.a_device_id, normalize(link.a_interface)))
    b = ports.get((link.b_device_id, normalize(link.b_interface)))

    a_vlans = port_vlans(a, defined.get(link.a_device_id, set())) if a else set()
    b_vlans = port_vlans(b, defined.get(link.b_device_id, set())) if b else set()

    if a_vlans and b_vlans:
        carried, mismatch = a_vlans & b_vlans, a_vlans ^ b_vlans
    else:
        # 片側しか Config を取り込んでいない場合。分かっている方をそのまま出す
        carried, mismatch = a_vlans or b_vlans, set()

    a_native = a.native_vlan if a else None
    b_native = b.native_vlan if b else None

    return TopologyEdgeView(
        id=link.id,
        source=link.a_device_id,
        target=link.b_device_id,
        source_interface=link.a_interface,
        target_interface=link.b_interface,
        origin=link.origin,
        protocol=link.protocol,
        label=link.label,
        confirmed_both_ways=link.confirmed_both_ways,
        vlans=sorted(carried),
        vlan_mismatch=sorted(mismatch),
        native_vlan=a_native or b_native,
        native_mismatch=bool(a_native and b_native and a_native != b_native),
        vrf=(a.vrf if a else None) or (b.vrf if b else None),
        source_side=link.a_side,
        target_side=link.b_side,
    )


def _interface_counts(db: Session) -> dict[int, int]:
    counts: dict[int, int] = {}
    for interface in db.scalars(select(Interface)):
        counts[interface.device_id] = counts.get(interface.device_id, 0) + 1
    return counts


def _auto_layout(
    devices: list[Device], orientation: str = "vertical"
) -> dict[int, tuple[float, float]]:
    """種別を段にして並べる初期配置。人が動かすまでの仮置き。

    `orientation="horizontal"` は段を縦の列にして、上位を左に置く。
    横長の紙や画面に貼るときは、こちらの方が収まりが良い。
    """
    tiers: dict[int, list[Device]] = {}
    for device in devices:
        tier = TIER_ORDER.get(device.device_type, 7)
        tiers.setdefault(tier, []).append(device)

    horizontal = orientation == "horizontal"
    positions: dict[int, tuple[float, float]] = {}
    for tier, members in sorted(tiers.items()):
        members.sort(key=lambda d: d.hostname)
        # 段の中央に寄せて並べる
        offset = -(len(members) - 1) / 2
        for index, device in enumerate(members):
            along = offset + index
            if horizontal:
                # 段を左から右へ。段の中は縦に並ぶので、箱の高さを基準に詰める
                positions[device.id] = (tier * _X_SPACING, along * _Y_SPACING)
            else:
                positions[device.id] = (along * _X_SPACING, tier * _Y_SPACING)
    return positions


def save_positions(
    db: Session,
    layer: str,
    positions: list[tuple[int, float, float]],
    *,
    site_id: int | None = None,
    pinned: bool | dict[int, bool] = True,
) -> int:
    """動かした機器の位置を、いま見ている拠点の図として保存する。

    動かした分だけを読む。以前は層の保存済み位置を全部読み込んでいたため、
    1台つまんで置き直すたびに全機器分の読み書きが走っていた（台数が増える
    ほど、動かすたびに固まる）。

    `site_id` がその図。空は全体図。対向機器もこの図の座標として残す。
    相手の拠点の図は別の行なので、上書きしない。

    `pinned` は人の意思かどうか。画面から動かした保存は True。最適化が
    整えただけの位置は False にして、次の最適化でも動けるようにする。
    """
    wanted = [device_id for device_id, _, _ in positions]
    existing = {
        node.device_id: node
        for node in db.scalars(
            select(TopologyNode).where(
                TopologyNode.layer == layer,
                TopologyNode.device_id.in_(wanted),
                _layout_of(site_id),
            )
        )
    }

    saved = 0
    for device_id, x, y in positions:
        node = existing.get(device_id)
        if node is None:
            node = TopologyNode(device_id=device_id, layer=layer, site_id=site_id)
            db.add(node)
        node.x = x
        node.y = y
        node.pinned = pinned[device_id] if isinstance(pinned, dict) else pinned
        saved += 1

    db.commit()
    return saved


def reset_positions(
    db: Session,
    layer: str,
    *,
    site_id: int | None = None,
    only: set[int] | None = None,
) -> int:
    """保存済みの配置を消して、自動レイアウトに戻す。

    消すのはいま見ている拠点の図だけ。`only` は検索で絞ったとき用。
    拠点を指定せず `only` だけだと、全体図のうちその機器だけを消す。
    """
    if only is not None and len(only) == 0:
        return 0
    stmt = select(TopologyNode).where(TopologyNode.layer == layer, _layout_of(site_id))
    if only is not None:
        stmt = stmt.where(TopologyNode.device_id.in_(only))
    nodes = list(db.scalars(stmt))
    for node in nodes:
        db.delete(node)
    db.commit()
    return len(nodes)


#: 人が図を揃えるときのマス目。画面のグリッド・ドラッグの吸着と同じ値。
LAYOUT_GRID = 40.0
#: この距離以内なら同じ段・同じ列とみなして揃える（マス目1つ弱）
_ALIGN_WITHIN = 36.0
#: 整頓で動かしてよい上限（軸ごと）。これを超えると「作り直し」に見える
_POLISH_MAX_MOVE = 80.0
#: 箱（190）＋マス目1つ分。これより近いと重なって見える
_MIN_GAP_X = 230.0
#: 箱の高さ（約78）＋マス目1つ分
_MIN_GAP_Y = 120.0


def _snap_to_grid(value: float, grid: float = LAYOUT_GRID) -> float:
    return round(value / grid) * grid


def _snap_at_least(value: float, floor: float, grid: float = LAYOUT_GRID) -> float:
    """マス目に乗せつつ、floor 未満にならない位置へ切り上げる。"""
    snapped = _snap_to_grid(value, grid)
    if snapped + 1e-9 >= floor:
        return snapped
    return ceil(floor / grid - 1e-12) * grid


def _clamp_move(delta: float, limit: float = _POLISH_MAX_MOVE) -> float:
    if delta > limit:
        return limit
    if delta < -limit:
        return -limit
    return delta


def _align_axis(nodes: list[TopologyNodeView], *, axis: str, threshold: float) -> None:
    """近い段・列を揃える。同じ列の機器を同じ段へ潰して横に飛ばさない。"""
    if len(nodes) < 2:
        return
    other = "y" if axis == "x" else "x"
    ordered = sorted(nodes, key=lambda node: (getattr(node, axis), node.device_id))
    groups: list[list[TopologyNodeView]] = [[ordered[0]]]
    for node in ordered[1:]:
        group = groups[-1]
        start = getattr(group[0], axis)
        prev = getattr(group[-1], axis)
        value = getattr(node, axis)
        if value - prev <= threshold and value - start <= threshold:
            other_v = getattr(node, other)
            stacked = any(abs(getattr(member, other) - other_v) <= 12.0 for member in group)
            if stacked:
                groups.append([node])
                continue
            group.append(node)
        else:
            groups.append([node])
    for group in groups:
        mid = sorted(getattr(member, axis) for member in group)[len(group) // 2]
        target = _snap_to_grid(mid)
        for member in group:
            setattr(member, axis, target)


def _unstick_along(
    nodes: list[TopologyNodeView],
    *,
    axis: str,
    min_gap: float,
    origins: dict[int, tuple[float, float]] | None = None,
) -> set[int]:
    """重なっている（または近すぎる）機器だけ、軸方向に空ける。

    一気に空けられるときだけ動かす。足りない押しを何度も繰り返すと、
    押すたびに図が変わるので、足りないときは触らない。

    同じマスに積み重なっている（gap < LAYOUT_GRID）ときは、前の点から
    min_gap まで一気に離す。返り値はその強制移動をした device_id。
    呼び出し側で移動上限の clamp を外さないと、また同じマスに潰れる。
    """
    forced: set[int] = set()
    buckets: dict[float, list[TopologyNodeView]] = {}
    other = "y" if axis == "x" else "x"
    for node in nodes:
        buckets.setdefault(getattr(node, other), []).append(node)
    for group in buckets.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda node: (getattr(node, axis), node.device_id))
        if axis == "x" and origins:
            xs = [origins[node.device_id][0] for node in group]
            ys = [origins[node.device_id][1] for node in group]
            if max(xs) - min(xs) <= 12.0 and max(ys) - min(ys) > max(xs) - min(xs):
                # もともと同じ列。同じ段に潰れてても横へ飛ばさない。
                continue
        for index in range(1, len(group)):
            prev = getattr(group[index - 1], axis)
            current = getattr(group[index], axis)
            gap = current - prev
            if gap >= min_gap:
                continue
            needed = min_gap - gap
            if gap < LAYOUT_GRID:
                target = prev + min_gap
                setattr(group[index], axis, _snap_at_least(target, prev + min_gap))
                forced.add(group[index].device_id)
                continue
            if needed <= _POLISH_MAX_MOVE:
                target = prev + min_gap
                setattr(group[index], axis, _snap_at_least(target, prev + min_gap))
    return forced


def _polish_layout(
    nodes: list[TopologyNodeView],
    *,
    edges: list[TopologyEdgeView] | None = None,
    owned: set[int] | None = None,
) -> None:
    """人が置いた配置を、マス目・揃い・近すぎる重なりだけ整える。

    ツリーを組み直さない。初期配置へ戻すのはリセットの仕事。
    動かす量は軸ごとにマス目2つ分まで。足りない押しを何度も繰り返さない
    （同じ図で二度押しても、位置が変わらないこと）。
    積み重なりをほどいた機器だけは、ほどいた間隔を clamp で潰さない。
    """
    if len(nodes) < 2:
        if nodes:
            nodes[0].x = _snap_to_grid(nodes[0].x)
            nodes[0].y = _snap_to_grid(nodes[0].y)
        return

    origin = {node.device_id: (node.x, node.y) for node in nodes}

    _align_axis(nodes, axis="y", threshold=_ALIGN_WITHIN)
    _align_axis(nodes, axis="x", threshold=_ALIGN_WITHIN)

    forced_x = _unstick_along(nodes, axis="x", min_gap=_MIN_GAP_X, origins=origin)
    forced_y = _unstick_along(nodes, axis="y", min_gap=_MIN_GAP_Y, origins=origin)

    for node in nodes:
        ox, oy = origin[node.device_id]
        # 軸ごとに切る。距離で両方を縮めると、揃えた段まで戻る
        if node.device_id not in forced_x:
            node.x = ox + _clamp_move(node.x - ox)
        if node.device_id not in forced_y:
            node.y = oy + _clamp_move(node.y - oy)
        node.x = _snap_to_grid(node.x)
        node.y = _snap_to_grid(node.y)

    if edges:
        _uncross_fans(nodes, edges, owned=owned or {node.device_id for node in nodes})
        for node in nodes:
            node.x = _snap_to_grid(node.x)
            node.y = _snap_to_grid(node.y)


def _uncross_fans(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView],
    *,
    owned: set[int],
) -> None:
    """同じ列・段に並んだ相手を、ハブのポート順に入れ替えて交差を減らす。

    位置の集合はそのまま。誰がどのスロットにいるかだけ入れ替える
    （2層グラフの交差削減）。owned の機器だけ動かす。
    """
    if len(nodes) < 2 or not edges:
        return
    by_id = {node.device_id: node for node in nodes}

    def port_from(hub: int, other: int) -> str:
        for edge in edges:
            if edge.source == hub and edge.target == other:
                return edge.source_interface
            if edge.target == hub and edge.source == other:
                return edge.target_interface
        return ""

    for hub in nodes:
        rights: list[TopologyNodeView] = []
        lefts: list[TopologyNodeView] = []
        belows: list[TopologyNodeView] = []
        aboves: list[TopologyNodeView] = []
        seen: set[int] = set()
        for edge in edges:
            other_id = (
                edge.target
                if edge.source == hub.device_id
                else edge.source
                if edge.target == hub.device_id
                else None
            )
            if other_id is None or other_id in seen:
                continue
            other = by_id.get(other_id)
            if other is None:
                continue
            seen.add(other_id)
            dx = other.x - hub.x
            dy = other.y - hub.y
            if abs(dx) >= abs(dy) and dx > 80:
                rights.append(other)
            elif abs(dx) >= abs(dy) and dx < -80:
                lefts.append(other)
            elif dy > 80:
                belows.append(other)
            elif dy < -80:
                aboves.append(other)
        for group, along in ((rights, "y"), (lefts, "y"), (belows, "x"), (aboves, "x")):
            movable = [node for node in group if node.device_id in owned]
            if len(movable) < 2:
                continue
            across = "x" if along == "y" else "y"
            span = max(getattr(node, across) for node in movable) - min(
                getattr(node, across) for node in movable
            )
            # 同じ列・段にいない相手まで入れ替えると、揃えた行が崩れる
            if span > LAYOUT_GRID:
                continue
            slots = sorted(getattr(node, along) for node in movable)
            ordered = sorted(
                movable,
                key=lambda node: (
                    port_key(port_from(hub.device_id, node.device_id)),
                    node.hostname,
                ),
            )
            for node, slot in zip(ordered, slots, strict=True):
                setattr(node, along, slot)


def arrange_positions(
    db: Session,
    layer: str,
    *,
    site_id: int | None = None,
    q: str | None = None,
    scope: str = SCOPE_PEERS,
    orientation: str = "vertical",
) -> int:
    """いま見えている範囲を、人が置いた位置を起点に整える。

    マス目に乗せ、近い段・列を揃え、重なりを少し空ける。木を組み直して
    初期配置へ戻すのではない。消して自動配置に戻すのはリセットの仕事。

    保存するのはいま見ている拠点の図。対向もこの図の座標として残す。
    相手の拠点の図は site_id が違う行なので、上書きしない。
    """
    draw = scoped_device_ids(db, site_id=site_id, q=q)
    graph = get_graph(db, layer, orientation=orientation, only=draw, site_id=site_id)
    graph = scope_graph(
        graph,
        site_id=site_id,
        q=q,
        scope=scope,
        orientation=orientation,
        rank=network_rank(db) if draw is not None else None,
    )

    owned = {n.device_id for n in graph.nodes}
    if not owned:
        return 0
    _polish_layout(list(graph.nodes), edges=graph.edges, owned=owned)

    placed = [(n.device_id, n.x, n.y) for n in graph.nodes]
    if not placed:
        return 0
    return save_positions(db, layer, placed, site_id=site_id, pinned=True)


@dataclass
class TopologyStats:
    device_count: int
    link_count: int
    discovered_links: int
    manual_links: int
    confirmed_links: int
    #: 1本も線が繋がっていない機器。多いときは頭から少しだけ
    isolated_devices: list[str]
    #: 繋がっていない機器の総数（isolated_devices は途中で切っている）
    isolated_count: int = 0


#: 未接続の機器を、名前で見せる上限。全部返しても読めないし、
#: 16万台の環境では応答が数MBになって画面が固まる
_ISOLATED_SAMPLE = 100


def get_stats(db: Session, layer: str = DEFAULT_LAYER) -> TopologyStats:
    """図の要約。数えるだけなので、図そのものは組み立てない。

    以前は全体のグラフを作ってから数えていた。数えるために16万台分の
    ノードとエッジを作ることになり、15秒かかっていた。数はすべて SQL の
    集計で出る。
    """
    if layer != DEFAULT_LAYER:
        # 論理図など、SQL で数えられない層は今までどおり組み立てて数える
        graph = get_graph(db, layer)
        connected = {e.source for e in graph.edges} | {e.target for e in graph.edges}
        isolated = sorted(n.hostname for n in graph.nodes if n.device_id not in connected)
        return TopologyStats(
            device_count=len(graph.nodes),
            link_count=len(graph.edges),
            discovered_links=sum(1 for e in graph.edges if e.origin is LinkOrigin.DISCOVERED),
            manual_links=sum(1 for e in graph.edges if e.origin is LinkOrigin.MANUAL),
            confirmed_links=sum(1 for e in graph.edges if e.confirmed_both_ways),
            isolated_devices=isolated[:_ISOLATED_SAMPLE],
            isolated_count=len(isolated),
        )

    alive = Device.status != DeviceStatus.RETIRED
    device_count = db.scalar(select(func.count()).select_from(Device).where(alive)) or 0

    link_count = db.scalar(select(func.count()).select_from(Link)) or 0
    discovered = (
        db.scalar(
            select(func.count()).select_from(Link).where(Link.origin == LinkOrigin.DISCOVERED)
        )
        or 0
    )
    manual = (
        db.scalar(select(func.count()).select_from(Link).where(Link.origin == LinkOrigin.MANUAL))
        or 0
    )
    confirmed = (
        db.scalar(select(func.count()).select_from(Link).where(Link.confirmed_both_ways.is_(True)))
        or 0
    )

    linked = select(Link.a_device_id).union(select(Link.b_device_id)).subquery()
    lonely = select(Device.hostname).where(alive, Device.id.notin_(select(linked.c[0])))
    isolated_count = db.scalar(select(func.count()).select_from(lonely.subquery())) or 0
    isolated = list(db.scalars(lonely.order_by(Device.hostname).limit(_ISOLATED_SAMPLE)))

    return TopologyStats(
        device_count=device_count,
        link_count=link_count,
        discovered_links=discovered,
        manual_links=manual,
        confirmed_links=confirmed,
        isolated_devices=isolated,
        isolated_count=isolated_count,
    )


@dataclass
class VlanView:
    """VLAN 1つ分。どこまで伸びているかを見るための情報。"""

    vlan_id: int
    name: str | None  # 表示に使う名前（台帳があればそちら、無ければ実機の名前）
    purpose: str | None = None
    owner: str | None = None
    subnet: str | None = None
    in_catalog: bool = False  # 台帳に登録済みか。未登録は「素性が分からない VLAN」
    observed_names: list[str] = field(default_factory=list)  # 実機に書かれている名前
    device_ids: list[int] = field(default_factory=list)
    device_names: list[str] = field(default_factory=list)
    defined_device_ids: list[int] = field(default_factory=list)  # VLAN を定義している機器
    access_port_count: int = 0
    trunk_port_count: int = 0
    native_port_count: int = 0


def vlan_summary(db: Session) -> list[VlanView]:
    """VLAN を、載っている機器つきで並べる。

    「VLAN 20 はどこまで伸びているのか」に答えるための一覧。台帳（人が書いた
    用途）と実機の観測値を突き合わせ、どちらか一方にしか無いものも落とさない。
    台帳に無い VLAN は素性の分からないセグメントなので、むしろ出す価値がある。
    """
    hostnames = {d.id: d.hostname for d in db.scalars(select(Device))}
    catalog = {row.vlan_id: row for row in db.scalars(select(Vlan))}
    defined = _defined_vlans(db)
    views: dict[int, VlanView] = {}

    def view_for(vlan_id: int) -> VlanView:
        view = views.get(vlan_id)
        if view is None:
            entry = catalog.get(vlan_id)
            view = VlanView(
                vlan_id=vlan_id,
                name=entry.name if entry else None,
                purpose=entry.purpose if entry else None,
                owner=entry.owner if entry else None,
                subnet=entry.subnet if entry else None,
                in_catalog=entry is not None,
            )
            views[vlan_id] = view
        return view

    for vlan_id in catalog:
        view_for(vlan_id)

    for row in db.scalars(select(DeviceVlan).order_by(DeviceVlan.vlan_id)):
        view = view_for(row.vlan_id)
        if row.name and row.name not in view.observed_names:
            view.observed_names.append(row.name)
        if row.device_id not in view.defined_device_ids:
            view.defined_device_ids.append(row.device_id)

    # ポートの設定から「実際に載っている機器」を出す。定義だけの VLAN と、
    # トランクを通っているだけの VLAN の両方が拾える
    for interface in db.scalars(select(Interface)):
        carried = port_vlans(interface, defined.get(interface.device_id, set()))
        for vlan_id in carried:
            view = view_for(vlan_id)
            if interface.device_id not in view.device_ids:
                view.device_ids.append(interface.device_id)
        if interface.vlan is not None:
            view_for(interface.vlan).access_port_count += 1
        if interface.native_vlan is not None:
            view_for(interface.native_vlan).native_port_count += 1
        for vlan_id in vlanlist.expand_column(
            interface.trunk_vlans, defined.get(interface.device_id, set())
        ):
            view_for(vlan_id).trunk_port_count += 1

    for view in views.values():
        for device_id in view.defined_device_ids:
            if device_id not in view.device_ids:
                view.device_ids.append(device_id)
        view.device_ids.sort()
        view.device_names = [hostnames.get(i, f"#{i}") for i in view.device_ids]
        # 台帳に名前が無ければ実機の名前で代用する。番号だけよりは判断できる
        if not view.name and view.observed_names:
            view.name = view.observed_names[0]

    return sorted(views.values(), key=lambda v: v.vlan_id)


@dataclass
class VrfView:
    """VRF 1つ分。同じ名前の VRF がどの機器に切られているかをまとめる。"""

    name: str
    rd: str | None = None
    description: str | None = None
    device_ids: list[int] = field(default_factory=list)
    device_names: list[str] = field(default_factory=list)
    interface_count: int = 0
    rd_mismatch: bool = False  # 機器ごとに RD が食い違っている


def vrf_summary(db: Session) -> list[VrfView]:
    """機器の中に切られた仮想ルータを、名前ごとにまとめて返す。

    同じ VRF 名が複数台にあるのが普通（拠点をまたいで同じ VRF を延ばす）ので、
    名前を軸にする。RD が機器ごとに食い違っていれば印を付ける。
    """
    hostnames = {d.id: d.hostname for d in db.scalars(select(Device))}
    views: dict[str, VrfView] = {}

    for row in db.scalars(select(DeviceVrf).order_by(DeviceVrf.name)):
        view = views.setdefault(row.name, VrfView(name=row.name))
        if row.rd and view.rd and row.rd != view.rd:
            view.rd_mismatch = True
        view.rd = view.rd or row.rd
        view.description = view.description or row.description
        if row.device_id not in view.device_ids:
            view.device_ids.append(row.device_id)
            view.device_names.append(hostnames.get(row.device_id, f"#{row.device_id}"))

    for interface in db.scalars(select(Interface).where(Interface.vrf.is_not(None))):
        view = views.get(interface.vrf)
        if view is not None:
            view.interface_count += 1

    return sorted(views.values(), key=lambda v: v.name)


def suggested_canvas_size(node_count: int) -> tuple[int, int]:
    """自動配置したときにだいたい収まる大きさ。画面側の初期表示に使う。"""
    columns = max(1, ceil(node_count**0.5))
    return int(columns * _X_SPACING + 200), int(len(TIER_ORDER) * _Y_SPACING + 200)
