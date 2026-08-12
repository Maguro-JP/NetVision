"""トポロジ図の組み立て。

自動推定した結線を土台に、人が配置を直して図を仕上げる前提で作っている。
- 位置は保存され、機器が増えても既存の配置は動かさない
- 未配置のノードだけ、種別を段にした自動レイアウトで初期位置を与える
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceType, DeviceVlan, DeviceVrf, Vlan
from app.models.topology import Interface, Link, LinkOrigin, TopologyNode
from app.services.configparse import expand, normalize, vlanlist
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

# 自動配置の間隔。機器の箱（190x78）に加えて、ポート名を置く余白を見込む。
# ここが詰まっていると、自動生成した図でラベルの置き場所が無くなる
_X_SPACING = 300.0
_Y_SPACING = 200.0

# 絞り込んだときの並べ直しで、1段に並べる上限と折り返した段の間隔
_WRAP = 6
_WRAP_SPACING = 130.0


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
class TopologyGraph:
    layer: str
    nodes: list[TopologyNodeView] = field(default_factory=list)
    edges: list[TopologyEdgeView] = field(default_factory=list)
    unplaced_count: int = 0


def scoped_device_ids(
    db: Session, *, site_id: int | None = None, q: str | None = None
) -> set[int] | None:
    """図に要る機器だけを先に絞り込む。

    拠点を選んで見るとき、必要なのはその拠点の機器と、そこから線が
    出ている相手だけ。それ以外まで読み込むと、全国規模では図を1つ出す
    のに何分もかかる（16万台では返ってこなかった）。

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
    inside = set(db.scalars(stmt))
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


def get_graph(
    db: Session,
    layer: str = DEFAULT_LAYER,
    *,
    include_retired: bool = False,
    orientation: str = "vertical",
    only: set[int] | None = None,
) -> TopologyGraph:
    if layer == "logical":
        # 論理図はトンネルから組み立てる。物理の結線とは別の話なので、
        # 同じ関数の中で分岐させて、呼び出し側（画面・書き出し）は同じままにする
        from app.services import topology_logical

        graph = topology_logical.build(
            db, include_retired=include_retired, orientation=orientation
        ).graph
        return _apply_saved_positions(db, graph)

    device_query = select(Device).options(selectinload(Device.contracts), selectinload(Device.site))
    if only is not None:
        # 絞り込みが決まっているなら、その分だけ読む（scoped_device_ids を参照）
        device_query = device_query.where(Device.id.in_(only))
    devices = list(db.scalars(device_query))
    if not include_retired:
        devices = [d for d in devices if d.status is not DeviceStatus.RETIRED]

    visible_ids = {d.id for d in devices}

    positions = {
        node.device_id: node
        for node in db.scalars(select(TopologyNode).where(TopologyNode.layer == layer))
    }
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

    placeholders = _auto_layout(
        [d for d in devices if d.id not in positions], orientation=orientation
    )

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

    return TopologyGraph(
        layer=layer,
        nodes=nodes,
        edges=edges,
        unplaced_count=sum(1 for n in nodes if not n.placed),
    )


def _apply_saved_positions(db: Session, graph: TopologyGraph) -> TopologyGraph:
    """人が動かした位置を反映する。図の種類が違っても、直した配置は残す。"""
    saved = {
        node.device_id: node
        for node in db.scalars(select(TopologyNode).where(TopologyNode.layer == graph.layer))
    }
    for node in graph.nodes:
        position = saved.get(node.device_id)
        if position is not None:
            node.x, node.y, node.placed = position.x, position.y, True
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
        # 上下は網全体で見る。渡されていなければ手元のぶんで求めるが、
        # 絞り込んだあとのグラフで求めると、その範囲でたまたま真ん中にいる
        # 機器が最上位になってしまう
        rank=rank if rank is not None else hierarchy_rank(graph),
        inside=inside,
        orientation=orientation,
    )
    for node in nodes:
        # 対向として足しただけの機器は、図の上で区別できるようにする
        node.outside = node.device_id not in inside
        node.hidden_peers = hidden.get(node.device_id, 0)

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


def _compact(
    nodes: list[TopologyNodeView],
    edges: list[TopologyEdgeView] | None = None,
    *,
    rank: dict[int, int] | None = None,
    inside: set[int] | None = None,
    orientation: str = "vertical",
) -> None:
    """絞り込んだ分だけで、上下（左右）の関係が分かる木に並べ直す。

    座標は網全体を前提に振ってあるので、数台に絞ると機器が散らばり、
    線を目で追えなくなる。ここで残った機器だけを並べ直す。

    並べ方は木。上位を上（横向きなら左）に置き、その配下を1段下へ広げる。
    同じ段に上位も下位も混ぜると、線が機器の間を横切って重なってしまう。

    人が動かして保存した配置が混ざっている場合は何もしない。
    直した配置を勝手に戻すと、直した意味が無くなる。
    """
    if not nodes or any(node.placed for node in nodes):
        return

    everything = {node.device_id: node for node in nodes}
    # 自拠点の機器だけで木を組む。対向を混ぜて組むと、対向を脇へ寄せたあとに
    # 自拠点の側が歯抜けになって、真ん中が空いた図になる
    tree_nodes = [n for n in nodes if not inside or n.device_id in inside] or nodes
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
    # 上位側から見たポート名。配下をポート順に並べると、線が交差しにくく、
    # 図の並びと実機のポート番号の並びも揃う
    port_between: dict[tuple[int, int], str] = {}
    for edge in graph.edges:
        port_between[(edge.source, edge.target)] = edge.source_interface
        port_between[(edge.target, edge.source)] = edge.target_interface
    order = sorted(
        by_id,
        key=lambda device_id: (
            rank.get(device_id, 0),
            _tier_of(by_id[device_id]),
            by_id[device_id].hostname,
        ),
    )

    # 上位から幅優先でたどって親子を決める。段（種別）だけで決めると、
    # 拠点ルータと支社ルータのように同じ種別が縦に並ぶ構成を表現できない
    depth: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    seen: set[int] = set()
    roots: list[int] = []
    for start in order:
        if start in seen:
            continue
        roots.append(start)
        seen.add(start)
        depth[start] = 0
        queue = [start]
        while queue:
            current = queue.pop(0)
            ordered = sorted(
                neighbours.get(current, []),
                key=lambda i: (
                    port_key(port_between.get((current, i), "")),
                    by_id[i].hostname,
                ),
            )
            for neighbour in ordered:
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                depth[neighbour] = depth[current] + 1
                children.setdefault(current, []).append(neighbour)
                queue.append(neighbour)

    # 葉から順に横位置を決め、親は子の中央に置く。これで線が交差しない
    slot = 0.0
    across: dict[int, float] = {}

    def place(device_id: int) -> float:
        nonlocal slot
        kids = children.get(device_id, [])
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

    horizontal = orientation == "horizontal"
    for device_id, node in by_id.items():
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
        )


# 機器の箱は、結線が多いほど横に広がる（画面側の widthFor と同じ規則）。
# 線を1本ずつ別の通り道に沿わせるための幅も要る。
# どちらも見込まずに対向を置くと、箱の右端が対向側へ食い込み、
# 線がいったん行き過ぎてから戻る形になって、その戻りが交差に見える。
_BOX_WIDTH = 190.0
_BOX_GROW = 55.0
_BOX_MAX = 620.0
_LANE_GAP = 16.0


def _room_for(links: int) -> float:
    """配下を何台ぶら下げるかから、右へ余分に空ける幅を出す。"""
    if links <= 0:
        return 0.0
    grown = min(_BOX_MAX, _BOX_WIDTH + max(0, links - 3) * _BOX_GROW)
    return (grown - _BOX_WIDTH) + links * _LANE_GAP


def _place_peers(
    by_id: dict[int, TopologyNodeView],
    graph: TopologyGraph,
    inside: set[int],
    rank: dict[int, int],
    *,
    horizontal: bool,
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
    left = min(node.x for node in own)
    right = max(node.x for node in own)
    top = min(node.y for node in own)
    middle = (left + right) / 2
    own_rank = min(rank.get(node.device_id, 0) for node in own)

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
        level = rank.get(device_id, own_rank)
        if level < own_rank:
            above.append(node)
        elif level > own_rank:
            below.append(node)
        else:
            beside.append(node)

    for group in (above, beside, below):
        group.sort(key=lambda node: node.hostname)

    for index, node in enumerate(above):
        offset = (index - (len(above) - 1) / 2) * _X_SPACING
        node.x, node.y = middle + offset, top - _Y_SPACING

    for index, node in enumerate(beside):
        node.x, node.y = right + (index + 1) * _X_SPACING * 1.6, top

    # 配下は右へ縦に並べる。斜め下に伸ばすと、台数が増えたとき紙に収まらない
    column = right + _X_SPACING * 1.6 + _room_for(len(below))
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


def save_positions(db: Session, layer: str, positions: list[tuple[int, float, float]]) -> int:
    """動かした機器の位置を保存する。

    動かした分だけを読む。以前は層の保存済み位置を全部読み込んでいたため、
    1台つまんで置き直すたびに全機器分の読み書きが走っていた（台数が増える
    ほど、動かすたびに固まる）。
    """
    wanted = [device_id for device_id, _, _ in positions]
    existing = {
        node.device_id: node
        for node in db.scalars(
            select(TopologyNode).where(
                TopologyNode.layer == layer, TopologyNode.device_id.in_(wanted)
            )
        )
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


def reset_positions(db: Session, layer: str, *, only: set[int] | None = None) -> int:
    """保存済みの配置を消して、自動レイアウトに戻す。

    `only` を渡すと、その機器の分だけ消す。拠点を1つ見ている最中の
    「リセット」で、他の拠点で直した配置まで消えるのは行き過ぎなため。
    """
    stmt = select(TopologyNode).where(TopologyNode.layer == layer)
    if only is not None:
        stmt = stmt.where(TopologyNode.device_id.in_(only))
    nodes = list(db.scalars(stmt))
    for node in nodes:
        db.delete(node)
    db.commit()
    return len(nodes)


def arrange_positions(
    db: Session,
    layer: str,
    *,
    site_id: int | None = None,
    q: str | None = None,
) -> int:
    """いま見えている範囲を、自動配置で並べ直す。

    手で置いていくとどうしてもずれる。並べ直しは自動配置がやることなので、
    その範囲の手動配置を外して自動配置に任せる。

    結果を保存しないのは、保存すると並べ直しが止まるため。図は「保存済みの
    配置があればそれを尊重する」作りで（直した配置を勝手に戻さないため）、
    自動配置の結果を保存すると、以後この範囲は並べ直されなくなる。
    対向の機器を保存すると、その機器自身の拠点の図まで、こちらの都合で
    決めた位置に変わってしまう問題もある。

    戻り値は外した手動配置の数。
    """
    only = scoped_device_ids(db, site_id=site_id, q=q)
    return reset_positions(db, layer, only=only)


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

    from sqlalchemy import func

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
