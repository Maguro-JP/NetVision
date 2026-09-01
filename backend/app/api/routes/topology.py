from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, Vlan
from app.models.topology import Link, LinkOrigin
from app.schemas.topology import (
    ArrangePositions,
    GroupCreate,
    GroupUpdate,
    LinkCreate,
    LinkRead,
    LinkUpdate,
    NodePosition,
    PortLabelRead,
    RebuildResult,
    SavePositions,
    TopologyGraphRead,
    TopologyGroupRead,
    TopologyStatsRead,
    VlanCatalogCreate,
    VlanCatalogRead,
    VlanCatalogWrite,
    VlanRead,
    VrfRead,
)
from app.services import configs as config_service
from app.services import spaceview, topology_export, topology_routes, topology_view
from app.services import topology as service
from app.services import vlans as vlan_service

router = APIRouter(prefix="/topology", tags=["topology"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=TopologyGraphRead)
def get_topology(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER, description="physical / l2 / l3 など"),
    include_retired: bool = False,
    orientation: str = Query(
        default="vertical",
        description="自動配置の向き。vertical = 上下 / horizontal = 左右",
    ),
    site_id: int | None = Query(default=None, description="この拠点の機器に絞る"),
    q: str | None = Query(default=None, description="機器名・拠点名・管理IPで探す"),
    scope: str = Query(
        default=service.SCOPE_PEERS,
        description="絞ったときにどこまで出すか。peers = 対向まで / site = 絞った分だけ",
    ),
    simple: bool = Query(
        default=False,
        description="機器名と結線だけの図。箱が小さくなるので、線の引き方も変わる",
    ),
) -> TopologyGraphRead:
    """図に描くためのノードと結線を返す。位置は保存済みのものを優先する。

    拠点が多い環境では全体図は使わない。`site_id` や `q` で絞った分だけを
    返し、既定では対向（直接つながっている相手）までを足す。
    """
    # 絞り込みが決まっているなら、必要な機器だけ読む。全部読んでから
    # 捨てる作りだと、全国規模では図を1つ出すのに何分もかかる
    only = service.scoped_device_ids(db, site_id=site_id, q=q)
    # 物理の全体図だけ止める。L2 / L3 / 論理は中身が絞られるので、
    # 台帳全体の台数では判断しない（論理は全国のトンネルを一度に見たい）
    # 台数が多いと全体図は描けない。ただし「出せません」とだけ返すと、
    # 台帳と結線表を入れた直後に、何も出ない図を見ることになる。
    # こちらで線の多いところを選んで出し、絞り込みの入口にする
    auto_scoped = (
        only is None
        and layer == service.DEFAULT_LAYER
        and service.unscoped_graph_too_wide(db, include_retired=include_retired)
    )
    if auto_scoped:
        only = service.busiest_device_ids(db, include_retired=include_retired)
    graph = service.load_diagram(
        db,
        layer,
        include_retired=include_retired,
        orientation=orientation,
        site_id=site_id,
        q=q,
        scope=scope,
        only=only if auto_scoped else None,
    )
    # 線と箱は、書き出しと同じところで決める。画面はそれを描くだけにして、
    # 同じ台帳から違う図が2つ出ないようにする
    drawn = topology_routes.compute(graph, simple=simple)
    for node in graph.nodes:
        box = drawn["boxes"].get(node.device_id)
        if box:
            node.width, node.height = box[2], box[3]
    for edge in graph.edges:
        wire = drawn["wires"].get(edge.id)
        if wire:
            edge.points = wire["points"]
            edge.source_side = wire["source_side"]
            edge.target_side = wire["target_side"]
        placed = drawn["labels"].get(edge.id) or {}
        edge.source_label = placed.get("source")
        edge.target_label = placed.get("target")

    if auto_scoped:
        graph.auto_scoped = True
        graph.note = (
            "台数が多いので、結線の多いところだけを出しています。"
            "拠点か機器名で絞ると、その範囲を出します"
        )
    return TopologyGraphRead.model_validate(graph, from_attributes=True)


@router.post("/groups", response_model=TopologyGroupRead)
def create_group(body: GroupCreate, db: DbSession) -> TopologyGroupRead:
    """選んだ機器を枠で囲む。"""
    try:
        group = service.create_group(
            db,
            body.layer,
            body.name,
            body.device_ids,
            plane=body.plane,
            color=body.color,
            parent_id=body.parent_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return TopologyGroupRead.model_validate(group, from_attributes=True)


@router.post("/groups/from-sites", response_model=list[TopologyGroupRead])
def groups_from_sites(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    device_ids: str = Query(default="", description="カンマ区切りの機器ID"),
) -> list[TopologyGroupRead]:
    """見えている機器を拠点ごとの枠にする。キャリア網を拠点単位で読むとき。"""
    ids = [int(part) for part in device_ids.split(",") if part.strip().isdigit()]
    return [
        TopologyGroupRead.model_validate(item, from_attributes=True)
        for item in service.groups_from_sites(db, layer, ids)
    ]


@router.post("/groups/from-planes", response_model=list[TopologyGroupRead])
def groups_from_planes(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    device_ids: str = Query(default="", description="カンマ区切りの機器ID"),
) -> list[TopologyGroupRead]:
    """見えている機器をキャリア網の段ごとの枠にする。"""
    ids = [int(part) for part in device_ids.split(",") if part.strip().isdigit()]
    return [
        TopologyGroupRead.model_validate(item, from_attributes=True)
        for item in service.groups_from_planes(db, layer, ids)
    ]


@router.post("/groups/from-racks", response_model=list[TopologyGroupRead])
def groups_from_racks(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    device_ids: str = Query(default="", description="カンマ区切りの機器ID"),
) -> list[TopologyGroupRead]:
    """見えている機器をラックごとの枠にする。"""
    ids = [int(part) for part in device_ids.split(",") if part.strip().isdigit()]
    return [
        TopologyGroupRead.model_validate(item, from_attributes=True)
        for item in service.groups_from_racks(db, layer, ids)
    ]


@router.patch("/groups/{group_id}", response_model=TopologyGroupRead)
def update_group(group_id: int, body: GroupUpdate, db: DbSession) -> TopologyGroupRead:
    try:
        group = service.update_group(
            db,
            group_id,
            name=body.name,
            plane=body.plane,
            color=body.color,
            device_ids=body.device_ids,
            parent_id=body.parent_id,
            clear_parent=body.clear_parent,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "枠が見つかりません")
    return TopologyGroupRead.model_validate(group, from_attributes=True)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, db: DbSession) -> None:
    if not service.delete_group(db, group_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "枠が見つかりません")


@router.get("/tunnels")
def list_tunnels(db: DbSession) -> dict:
    """トンネル（論理構成図の材料）の状況を返す。

    相手が特定できなかったものを必ず一緒に返す。黙って落とすと
    「トンネルが無い」と読まれるが、実際には台帳に無い相手（他社側の装置や
    クラウド）に張っていることが普通にある。
    """
    from app.services import topology_logical

    result = topology_logical.build(db)
    return {
        "tunnel_count": len(result.graph.edges),
        "device_count": len(result.graph.nodes),
        "unresolved": [
            {
                "device": item.device,
                "name": item.name,
                "destination": item.destination,
                "kind": item.kind,
            }
            for item in result.unresolved
        ],
    }


@router.get("/vlans", response_model=list[VlanRead])
def list_vlans(db: DbSession) -> list[VlanRead]:
    """定義されている VLAN を、載っている機器つきで返す。

    「VLAN 20 はどこまで伸びているのか」を見るための一覧。
    """
    return service.vlan_summary(db)


@router.get("/vlans/catalog", response_model=list[VlanCatalogRead])
def list_vlan_catalog(db: DbSession) -> list[Vlan]:
    """VLAN 台帳（人が用途を書く側）を返す。"""
    return vlan_service.list_vlans(db)


@router.put("/vlans/catalog/{vlan_id}", response_model=VlanCatalogRead)
def upsert_vlan_catalog(vlan_id: int, payload: VlanCatalogWrite, db: DbSession) -> Vlan:
    """VLAN の名前・用途を登録/変更する。

    ここに書いた内容は Config を取り込み直しても上書きされない。
    実機の設定と食い違っていれば、それは実機側が古いという情報になる。
    """
    if not 1 <= vlan_id <= 4094:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "VLAN は 1〜4094 です")
    return vlan_service.upsert(db, vlan_id, payload.model_dump(exclude_unset=True))


@router.post("/vlans/catalog", response_model=VlanCatalogRead, status_code=status.HTTP_201_CREATED)
def create_vlan_catalog(payload: VlanCatalogCreate, db: DbSession) -> Vlan:
    values = payload.model_dump(exclude_unset=True)
    vlan_id = values.pop("vlan_id")
    return vlan_service.upsert(db, vlan_id, values)


@router.delete("/vlans/catalog/{vlan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vlan_catalog(vlan_id: int, db: DbSession) -> None:
    """台帳から消すだけで、実機の設定や観測値には触らない。"""
    if not vlan_service.delete(db, vlan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"VLAN {vlan_id} は台帳にありません")


@router.post("/vlans/catalog/adopt", response_model=dict[str, int])
def adopt_observed_vlans(db: DbSession, overwrite: bool = False) -> dict[str, int]:
    """実機に書いてある VLAN 名を台帳へ下書きとして取り込む。

    数が多い現場で台帳を1から手で埋めるのは現実的ではないので、まず実機の名前で
    埋めてから用途を書き足す。既に書いてある行は既定では触らない。
    """
    result = vlan_service.adopt_observed(db, overwrite=overwrite)
    return {"created": result.created, "updated": result.updated}


@router.get("/vrfs", response_model=list[VrfRead])
def list_vrfs(db: DbSession) -> list[VrfRead]:
    """機器の中に切られた仮想ルーターを、名前ごとにまとめて返す。"""
    return service.vrf_summary(db)


class RoutesIn(BaseModel):
    """いま画面にある配置。動かしている途中の位置で線を引き直すのに使う。"""

    layer: str = service.DEFAULT_LAYER
    site_id: int | None = None
    q: str | None = None
    scope: str = service.SCOPE_PEERS
    simple: bool = False
    positions: list[NodePosition] = []


class WireOut(BaseModel):
    edge_id: int
    points: list[tuple[float, float]]
    source_side: str
    target_side: str
    #: 両端のポート名の置き場所。線と同じところで決めて画面へ渡す
    source_label: PortLabelRead | None = None
    target_label: PortLabelRead | None = None


class BoxOut(BaseModel):
    device_id: int
    width: float
    height: float


class RoutesOut(BaseModel):
    boxes: list[BoxOut]
    wires: list[WireOut]


@router.post("/routes", response_model=RoutesOut)
def compute_routes(body: RoutesIn, db: DbSession) -> RoutesOut:
    """動かした位置で、線を引き直す。

    線を引く決まりは書き出しの側だけに置いてある。画面が自分で引くと、
    同じ台帳から違う図が2つ出るため。動かしている最中の位置でも同じ形が
    要るので、ここで引いて返す。
    """
    graph = service.load_diagram(db, body.layer, site_id=body.site_id, q=body.q, scope=body.scope)
    moved = {item.device_id: (item.x, item.y) for item in body.positions}
    for node in graph.nodes:
        spot = moved.get(node.device_id)
        if spot:
            node.x, node.y = spot
    drawn = topology_routes.compute(graph, simple=body.simple)
    return RoutesOut(
        boxes=[
            BoxOut(device_id=device_id, width=box[2], height=box[3])
            for device_id, box in drawn["boxes"].items()
        ],
        wires=[
            WireOut(
                edge_id=edge_id,
                points=[(x, y) for x, y in wire["points"]],
                source_side=wire["source_side"],
                target_side=wire["target_side"],
                source_label=(drawn["labels"].get(edge_id) or {}).get("source"),
                target_label=(drawn["labels"].get(edge_id) or {}).get("target"),
            )
            for edge_id, wire in drawn["wires"].items()
        ],
    )


class ViewBoxIn(BaseModel):
    """画面が描いた箱1つ。左上と、実際の大きさ。"""

    device_id: int
    x: float
    y: float
    width: float
    height: float


class ViewWireIn(BaseModel):
    """画面が引いた線1本。折れ点と、どの辺から出ているか。"""

    edge_id: int
    points: list[tuple[float, float]]
    source_side: str = "right"
    target_side: str = "left"


class ViewIn(BaseModel):
    boxes: list[ViewBoxIn] = []
    wires: list[ViewWireIn] = []


class ViewToken(BaseModel):
    token: str


@router.post("/views", response_model=ViewToken)
def keep_view(body: ViewIn) -> ViewToken:
    """いま画面に出ている図の形を預かる。

    人は画面で配置と線を確かめてから「出力する」を押す。そこで別のものが
    出てくるなら、確かめた意味が無い。画面が引いた線をそのまま渡してもらい、
    書き出しはそれを描くだけにする。

    預かるのは形だけで、中身（機器名・型番・状態）は台帳から取り直す。
    画面から送られてきた文字をそのまま図に出すと、細工した文字が資料に
    載ることになる。
    """
    view = topology_view.ViewGeometry(
        boxes={
            box.device_id: topology_view.ViewBox(
                device_id=box.device_id,
                x=box.x,
                y=box.y,
                width=box.width,
                height=box.height,
            )
            for box in body.boxes
        },
        wires={
            wire.edge_id: topology_view.ViewWire(
                edge_id=wire.edge_id,
                points=[(x, y) for x, y in wire.points],
                source_side=wire.source_side,
                target_side=wire.target_side,
            )
            for wire in body.wires
            if len(wire.points) >= 2
        },
    )
    return ViewToken(token=topology_view.keep(view))


@router.get("/export.svg", response_class=Response)
def export_topology(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    title: str = "ネットワーク構成図",
    vlan: int | None = Query(default=None, description="この VLAN を持つ機器だけを描く"),
    vrf: str | None = Query(default=None, description="この VRF に属する機器だけを描く"),
    site_id: int | None = Query(default=None, description="この拠点の機器だけを描く"),
    style: str = Query(
        default="detail",
        description="detail = 型番や状態つき / simple = 機器名と結線だけ",
    ),
    port_style: str = Query(
        default="short",
        description="ポート名の出し方。full = そのまま / short = 略記 / number = 番号だけ",
    ),
    along_line: bool = Query(default=False, description="ポート名を線に沿わせて回転する"),
    show_peers: bool = Query(
        default=True,
        description="絞り込みの外に出た対向機器を簡易表示で残す（線ごと消さない）",
    ),
    orientation: str = Query(
        default="vertical",
        description="自動配置の向き。vertical = 上下 / horizontal = 左右",
    ),
    view: str | None = Query(
        default=None,
        description="画面が描いた図の札（/topology/views で預けたもの）。"
        "渡すと、その線と箱をそのまま描く",
    ),
) -> Response:
    """構成図を SVG で書き出す。

    設計書や報告書に貼るための出力。ベクタなので拡大・印刷しても潰れず、
    Word / PowerPoint / Excel はそのまま挿入できる（Office 2016 以降）。
    画面で整えた配置をそのまま使う。

    `style=simple` は機器名と線だけの図。作業手順書や現場での確認に使う想定で、
    型番・IP・保守状態・VLAN といった付帯情報を一切載せない。

    ポート名はそれぞれの機器側に置く。1本の線に2つ並べると「左が上位」の
    ような規則を覚える必要が出て、図の向きが変わると逆に読んでしまうため。

    絞り込みの外に出た対向機器は、破線の簡易表示として残す。線ごと消すと
    「その拠点はどこにも繋がっていない」図になってしまうため。
    """
    svg = topology_export.export_svg(
        db,
        layer,
        view=topology_view.take(view),
        title=title,
        vlan=vlan,
        vrf=vrf,
        site_id=site_id,
        style=style,
        port_style=port_style,
        along_line=along_line,
        show_peers=show_peers,
        orientation=orientation,
    )
    suffix = f"-vlan{vlan}" if vlan else ""
    if vrf:
        suffix += f"-{vrf}"
    if site_id:
        suffix += f"-site{site_id}"
    if style == "simple":
        suffix += "-simple"
    filename = f"topology-{layer}{suffix}.svg"
    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.xlsx", response_class=Response)
def export_link_table(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    site_id: int | None = Query(default=None, description="この拠点に掛かる結線だけを出す"),
) -> Response:
    """結線表を Excel で書き出す。

    図が要らない場面がある。作業指示・工事依頼・検収の突合で欲しいのは、
    並べ替えと絞り込みができる表なので、そちらは Excel で出す。

    拠点で絞る場合、片側でもその拠点に掛かっていれば出す。
    拠点をまたぐ回線こそ表で確認したいため。
    """
    from app.services import topology_table

    data = topology_table.to_xlsx(db, layer, site_id=site_id)
    name = f"links-{layer}{f'-site{site_id}' if site_id else ''}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/workbook.xlsx", response_class=Response)
def export_workbook(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    site_id: int | None = Query(default=None, description="この拠点に絞って出す"),
    scope: str = Query(default=service.SCOPE_PEERS),
    view: str | None = Query(
        default=None,
        description="画面が描いた図の札（/topology/views で預けたもの）",
    ),
) -> Response:
    """構成図・結線表・ポート一覧を1つの Excel にまとめて出す。

    構成図は SVG と同じ図を、Excel の図形として置く。掴んで動かせるし、色も
    変えられる。線1本まで画面と同じものが入る。

    拠点が多い環境では全体を1枚に描いても読めないので、拠点で絞って出す前提。
    """
    from app.services import topology_excel

    data = topology_excel.to_xlsx(
        db, layer, site_id=site_id, scope=scope, view=topology_view.take(view)
    )
    name = f"topology-{layer}{f'-site{site_id}' if site_id else ''}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/ports.xlsx", response_class=Response)
def export_port_map(
    db: DbSession,
    site_id: int | None = Query(default=None, description="この拠点の機器だけを出す"),
) -> Response:
    """機器ごとのポート割付を Excel で書き出す。

    結線表は「線」から見た表だが、増設のときに聞かれるのは機器から見た話で、
    何ポートあって、上位のどこへ上がっていて、下に何がぶら下がっていて、
    どこが空いているか。それを1枚目（一覧）と2枚目（結線図）で出す。
    """
    from app.services import topology_ports

    data = topology_ports.to_xlsx(db, site_id=site_id)
    name = f"ports{f'-site{site_id}' if site_id else ''}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/export.csv", response_class=Response)
def export_link_table_csv(
    db: DbSession,
    layer: str = Query(default=service.DEFAULT_LAYER),
    site_id: int | None = Query(default=None),
    encoding: str = Query(default="utf-8", description="utf-8 / shift_jis"),
) -> Response:
    """結線表を CSV で書き出す。Excel が無い環境向け。"""
    from app.services import topology_table

    data = topology_table.to_csv(db, layer, site_id=site_id, encoding=encoding)
    name = f"links-{layer}{f'-site{site_id}' if site_id else ''}.csv"
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/stats", response_model=TopologyStatsRead)
def get_stats(db: DbSession, layer: str = service.DEFAULT_LAYER) -> TopologyStatsRead:
    return TopologyStatsRead.model_validate(service.get_stats(db, layer), from_attributes=True)


class SpaceSiteRead(BaseModel):
    site_id: int | None
    name: str
    address: str | None
    device_count: int
    severity: str


class SpaceEdgeRead(BaseModel):
    a: int | None
    b: int | None
    count: int


class SpaceViewRead(BaseModel):
    sites: list[SpaceSiteRead]
    edges: list[SpaceEdgeRead]
    device_total: int
    link_total: int


@router.get("/space", response_model=SpaceViewRead)
def get_space(db: DbSession) -> SpaceViewRead:
    """眺めるための、拠点単位にまとめた姿。

    返る大きさは拠点数で決まり、機器が何台あっても変わらない。
    機器1台ずつを見たいときは、拠点を選んで通常のグラフを取る。
    """
    return SpaceViewRead.model_validate(spaceview.build(db), from_attributes=True)


@router.put("/positions", response_model=dict[str, int])
def save_positions(payload: SavePositions, db: DbSession) -> dict[str, int]:
    """人が動かしたノード位置を保存する。"""
    # 送られてきた機器だけ確かめる。全機器を読むと、1台動かすたびに
    # 台数分の読み込みが走る
    wanted = [p.device_id for p in payload.positions]
    known = set(db.scalars(select(Device.id).where(Device.id.in_(wanted))))
    unknown = [device_id for device_id in wanted if device_id not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"存在しない機器が含まれています: {unknown}",
        )

    saved = service.save_positions(
        db,
        payload.layer,
        [(p.device_id, p.x, p.y) for p in payload.positions],
        site_id=payload.site_id,
    )
    return {"saved": saved}


@router.delete("/positions", response_model=dict[str, int])
def reset_positions(
    db: DbSession,
    layer: str = service.DEFAULT_LAYER,
    site_id: int | None = None,
    q: str | None = None,
) -> dict[str, int]:
    """配置を自動レイアウトに戻す。

    拠点や検索を指定すればその範囲だけ、指定しなければ全体図だけ。
    1拠点を見ている最中のリセットで、他の拠点の図まで消えるのは行き過ぎ。
    """
    only = service.matching_device_ids(db, site_id=None, q=q) if q else None
    return {"reset": service.reset_positions(db, layer, site_id=site_id, only=only)}


@router.post("/positions/arrange", response_model=dict[str, int])
def arrange_positions(
    db: DbSession,
    layer: str = service.DEFAULT_LAYER,
    site_id: int | None = None,
    q: str | None = None,
    scope: str = Query(default=service.SCOPE_PEERS),
    orientation: str = Query(default="vertical"),
    body: ArrangePositions | None = None,
) -> dict[str, int]:
    """いま見えている範囲を、人の意思を残して完成形に整える。

    起点は画面の配置。未保存の移動を捨てて初期配置へ戻すのはリセット。
    人が置いた左右の並びは残し、近い段を揃え、マス目に乗せる。
    """
    canvas = [(p.device_id, p.x, p.y) for p in (body.positions if body else [])]
    return {
        "arranged": service.arrange_positions(
            db,
            layer,
            site_id=site_id,
            q=q,
            scope=scope,
            orientation=orientation,
            positions=canvas or None,
        )
    }


@router.post("/rebuild", response_model=RebuildResult)
def rebuild(db: DbSession) -> RebuildResult:
    """保存済みの Config を読み直して結線を作り直す。手で引いた線は残す。"""
    created, unresolved = config_service.rebuild_links(db)
    return RebuildResult(
        links_created=created,
        unresolved_neighbors=[
            {
                "local_device": n.local_device,
                "local_interface": n.local_interface,
                "remote_hostname": n.remote_hostname,
                "remote_platform": n.remote_platform,
            }
            for n in unresolved
        ],
    )


@router.get("/links", response_model=list[LinkRead])
def list_links(db: DbSession) -> list[Link]:
    return list(db.scalars(select(Link).order_by(Link.id)))


@router.post("/links", response_model=LinkRead, status_code=status.HTTP_201_CREATED)
def create_link(payload: LinkCreate, db: DbSession) -> Link:
    """人が図に引いた線を登録する。"""
    if payload.a_device_id == payload.b_device_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "同じ機器同士は結線できません")

    for device_id in (payload.a_device_id, payload.b_device_id):
        if db.get(Device, device_id) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"device {device_id} が存在しません"
            )

    link = config_service.upsert_link(
        db,
        payload.a_device_id,
        payload.a_interface,
        payload.b_device_id,
        payload.b_interface,
        origin=LinkOrigin.MANUAL,
    )
    if link is None:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "同じ結線が既に登録されています")

    link.label = payload.label
    db.commit()
    db.refresh(link)
    return link


def _load(db: Session, link_id: int) -> Link:
    link = db.get(Link, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"link {link_id} が見つかりません")
    return link


@router.patch("/links/{link_id}", response_model=LinkRead)
def update_link(link_id: int, payload: LinkUpdate, db: DbSession) -> Link:
    """結線を直す。端を別の機器へ付け替えることもできる。

    実機を見ながら図を直すとき、引き直すより端をつまんで動かす方が早い。
    ポート名は付け替えても引き継ぐ（同じケーブルの相手が変わっただけ、
    という直し方が多いため）。
    """
    link = _load(db, link_id)
    changes = payload.model_dump(exclude_unset=True)

    a_device = changes.get("a_device_id", link.a_device_id)
    b_device = changes.get("b_device_id", link.b_device_id)
    if a_device == b_device:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "同じ機器同士は結線できません")
    for device_id in (a_device, b_device):
        if device_id != link.a_device_id and device_id != link.b_device_id:
            if db.get(Device, device_id) is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"device {device_id} が存在しません"
                )

    a_port = changes.get("a_interface", link.a_interface)
    b_port = changes.get("b_interface", link.b_interface)
    duplicate = db.scalar(
        select(Link).where(
            Link.id != link.id,
            Link.a_device_id == a_device,
            Link.a_interface == a_port,
            Link.b_device_id == b_device,
            Link.b_interface == b_port,
        )
    )
    if duplicate is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "同じ結線が既にあります")

    for key, value in changes.items():
        setattr(link, key, value)
    # 端を付け替えたら、両側から確認できていたという印は外す
    if "a_device_id" in changes or "b_device_id" in changes:
        link.confirmed_both_ways = False
        link.origin = LinkOrigin.MANUAL

    db.commit()
    db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(link_id: int, db: DbSession) -> None:
    db.delete(_load(db, link_id))
    db.commit()


# ---------------------------------------------------------------------------
# 経路から結線を推定する
# ---------------------------------------------------------------------------


class LinkCandidateRead(BaseModel):
    """経路の情報から見つけた、線の候補。"""

    a_device_id: int
    a_hostname: str
    a_interface: str
    b_device_id: int
    b_hostname: str
    b_interface: str
    segment: str
    confidence: str
    why: str
    evidence: list[str]


class ApplyCandidatesBody(BaseModel):
    #: 入れる組。空なら言い切れるものだけ入れる
    pairs: list[tuple[int, int]] | None = None


@router.get("/link-candidates", response_model=list[LinkCandidateRead])
def list_link_candidates(db: DbSession) -> list[LinkCandidateRead]:
    """同じセグメントにいる組を、線の候補として返す。

    LLDP を切っている現場では、running-config だけが手元に来る。それでも
    L3 の情報は必ず書いてあるので、そこから引ける線を出す。
    """
    from app.services import l3links

    return [
        LinkCandidateRead(
            a_device_id=c.a_device_id,
            a_hostname=c.a_hostname,
            a_interface=c.a_interface,
            b_device_id=c.b_device_id,
            b_hostname=c.b_hostname,
            b_interface=c.b_interface,
            segment=c.segment,
            confidence=c.confidence,
            why=c.why,
            evidence=c.evidence,
        )
        for c in l3links.candidates(db)
    ]


@router.post("/link-candidates/apply", response_model=dict)
def apply_link_candidates(payload: ApplyCandidatesBody, db: DbSession) -> dict:
    """候補を線にする。組を選ばなければ、言い切れるものだけ入れる。"""
    from app.services import l3links

    created = l3links.apply(db, payload.pairs)
    return {"created": created}


# ---------------------------------------------------------------------------
# 線の両端で、設定がそろっているか
# ---------------------------------------------------------------------------


class LinkFindingRead(BaseModel):
    """1本の線についての指摘。どの線かが分からないと直しに行けない。"""

    link_id: int
    a_hostname: str
    a_interface: str
    b_hostname: str
    b_interface: str
    level: str
    title: str
    why: str
    evidence: list[str]


class LinkReportRead(BaseModel):
    links_seen: int
    links_with_config: int
    findings: list[LinkFindingRead]
    notes: list[str]


class VlanFindingRead(BaseModel):
    vlan_id: int
    level: str
    title: str
    why: str
    evidence: list[str]


class VlanReportRead(BaseModel):
    vlans_seen: int
    devices_with_vlans: int
    findings: list[VlanFindingRead]
    notes: list[str]


@router.get("/vlan-check", response_model=VlanReportRead)
def check_vlans(db: DbSession) -> VlanReportRead:
    """VLAN の名前が、機器と台帳でそろっているかを見る。

    番号は同じでも名前が食い違うと、作業の指示が通らなくなる。
    「10 番を止めて」が、別の機器では別の意味になる。
    """
    from app.services import vlanreview

    report = vlanreview.review(db)
    return VlanReportRead(
        vlans_seen=report.vlans_seen,
        devices_with_vlans=report.devices_with_vlans,
        findings=[
            VlanFindingRead(
                vlan_id=item.vlan_id,
                level=item.finding.level,
                title=item.finding.title,
                why=item.finding.why,
                evidence=item.finding.evidence,
            )
            for item in report.findings
        ],
        notes=report.notes,
    )


class StpFindingRead(BaseModel):
    level: str
    title: str
    why: str
    evidence: list[str]


class StpReportRead(BaseModel):
    devices_seen: int
    #: VLAN 番号 → 根になる機器。全体に効く書き方しか無いときは "all"
    roots: dict[str, str]
    findings: list[StpFindingRead]
    notes: list[str]


@router.get("/stp-check", response_model=StpReportRead)
def check_stp(db: DbSession) -> StpReportRead:
    """ループを止める木の根が、どこに立つかを機器をまたいで見る。

    1台の Config だけでは言えない。優先度は相手と比べてはじめて意味を
    持つので、根の位置は機器をまたがないと分からない。
    """
    from app.services import stpreview

    report = stpreview.review(db)
    return StpReportRead(
        devices_seen=report.devices_seen,
        roots={("all" if vlan is None else str(vlan)): host for vlan, host in report.roots.items()},
        findings=[
            StpFindingRead(
                level=item.level,
                title=item.title,
                why=item.why,
                evidence=item.evidence,
            )
            for item in report.findings
        ],
        notes=report.notes,
    )


@router.get("/link-check", response_model=LinkReportRead)
def check_links(db: DbSession) -> LinkReportRead:
    """引けている線を、両端の設定で突き合わせる。

    1台ずつの Config を見ても分からない壊れ方がある。片側だけトランク、
    片側だけ大きい MTU、片側だけ速度を固定。どれも片方の機器の中では
    正しいので、1台ずつ見ている限り見つからない。
    """
    from app.services import linkcheck

    report = linkcheck.review(db)
    return LinkReportRead(
        links_seen=report.links_seen,
        links_with_config=report.links_with_config,
        findings=[
            LinkFindingRead(
                link_id=item.link_id,
                a_hostname=item.a_hostname,
                a_interface=item.a_interface,
                b_hostname=item.b_hostname,
                b_interface=item.b_interface,
                level=item.finding.level,
                title=item.finding.title,
                why=item.finding.why,
                evidence=item.finding.evidence,
            )
            for item in report.findings
        ],
        notes=report.notes,
    )
