import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { DeviceType, TopologyEdge, TopologyGraph, TopologyNode } from "../api/types";
import DeviceNode, {
  SLOTS,
  anchorId,
  type DeviceNodeData,
  type Side,
} from "../components/DeviceNode";
import PortLabeledEdge, { type PortEdgeData } from "../components/PortLabeledEdge";
import { DEVICE_TYPE_LABEL } from "../lib/format";
import { MEDIA_COLOR, MEDIA_LABEL, mediaOf, type Media, type PortStyle } from "../lib/portlabel";
import { confirmLeave, useUnsavedWarning } from "../lib/unsaved";
import {
  applyPositions,
  canRedo,
  canUndo,
  capturePositions,
  changedIds,
  currentPositions,
  isDirty as historyIsDirty,
  pushStep,
  redo as redoHistory,
  startHistory,
  undo as undoHistory,
  type EditHistory,
} from "./topologyEditHistory";

const NODE_TYPES = { device: DeviceNode };
const EDGE_TYPES = { port: PortLabeledEdge };

const PORT_STYLES: { value: PortStyle; label: string; hint: string }[] = [
  { value: "short", label: "略記", hint: "GigabitEthernet1/0/24 → Gi1/0/24" },
  { value: "number", label: "番号だけ", hint: "種別は色で表します（Gi は青、Te は緑）" },
  { value: "full", label: "そのまま", hint: "Config に書かれている名前をそのまま出します" },
];

/** 表示の好みは人によって違う。毎回選び直さずに済むよう端末に覚えさせる。 */
const VIEW_KEY = "netvision.topology.view";

// これを超えたら、絞らずに全体を描かない。全国規模になると全体図は読めないし、
// 描くだけで重い。拠点を選ぶか、名前で探してもらう
const WIDE_LIMIT = 40;

/** 配置のマス目。整頓・ドラッグの吸着と揃える。 */
const LAYOUT_GRID = 40;

export function snapToGrid(value: number): number {
  return Math.round(value / LAYOUT_GRID) * LAYOUT_GRID;
}

/** 図をクリックした位置を、箱の左上としてマス目に乗せる。 */
export function placementFromClick(x: number, y: number): { x: number; y: number } {
  return { x: snapToGrid(x), y: snapToGrid(y) };
}

/** 線を通す道の間隔。狭いと線が重なって、広いと図が間延びする */
const LANE_GAP = 16;

interface ViewSettings {
  portStyle: PortStyle;
  alongLine: boolean;
  simple: boolean;
  /** 自動配置の向き。横長の紙や画面に貼るときは左右の方が収まる */
  orientation: "vertical" | "horizontal";
}

function loadView(): ViewSettings {
  const fallback: ViewSettings = {
    portStyle: "short",
    alongLine: false,
    simple: false,
    orientation: "vertical",
  };
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "{}");
    return { ...fallback, ...saved };
  } catch {
    return fallback;
  }
}

const LAYERS = [
  {
    value: "physical",
    label: "物理構成",
    hint: "ケーブル・LLDP の結線。機器がどう繋がっているか",
  },
  {
    value: "logical",
    label: "論理構成",
    hint: "IPsec / GRE などのトンネル。物理とは別の通り道",
  },
  {
    value: "l2",
    label: "L2 (VLAN)",
    hint: "VLAN が載っている結線だけ。同じ VLAN がどこまで伸びているか",
  },
  {
    value: "l3",
    label: "L3 (セグメント)",
    hint: "ルータ同士や IP 付きの結線。ルーティングの段",
  },
];

/** API のグラフを React Flow の形に変換する。
 *
 * `simple` は機器名と線だけの表示。現場で使う図に型番や保守状態が載っていると
 * 読み違いのもとになるので、用途で切り替えられるようにしてある。
 */
interface LabelOptions {
  portStyle: PortStyle;
  alongLine: boolean;
  editable: boolean;
}

/** 段が違うと見なす縦の差。これ未満なら横並びとして扱う。 */
const SAME_ROW = 60;

/** 横へ大きく離れていると見なす差。これを超えたら横から出す。 */
const ASIDE = 240;

/**
 * 機器の箱のどの辺から線を出すか。
 *
 * 人が描くときの判断に合わせる。真下にいる相手へは下から出して上へ入れる。
 * 同じ段に並んでいる相手（冗長構成やスタック間）は横どうしで結ぶ。
 *
 * 難しいのは、段が違ううえに横へも離れている相手——支社から見た各営業所が
 * これにあたる。下から出すと、自分の真下にある配下の機器へ向かう線を
 * 何本もまたぐことになる。横から出せばまたがずに済み、線も短い。
 * 横へ 1 列以上ずれている相手は、下ではなく横につなぐ。
 */
export function sideToward(from: TopologyNode, to: TopologyNode): Side {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dy) < SAME_ROW) return dx > 0 ? "right" : "left";
  if (Math.abs(dx) >= ASIDE) return dx > 0 ? "right" : "left";
  return dy > 0 ? "bottom" : "top";
}

/**
 * 各結線の引き出し口を決める。
 *
 * 人が指定していれば従い、していなければ位置関係から選ぶ。
 * 同じ辺に複数本来るときは3か所に散らす。同じ点から出すと線が重なって、
 * どのポートがどれか分からなくなる。
 */
export function assignAnchors(graph: TopologyGraph): {
  anchors: Map<number, { source: string; target: string }>;
  busiest: Map<number, number>;
  lanes: Map<number, number>;
} {
  const nodes = new Map(graph.nodes.map((n) => [n.device_id, n]));
  const out = new Map<number, { source: string; target: string }>();

  // まず、どの辺から出すかを決める
  const ends: { edge: number; device: number; side: Side; other: number }[] = [];
  for (const edge of graph.edges) {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (!source || !target) continue;
    ends.push({
      edge: edge.id,
      device: edge.source,
      side: (edge.source_side as Side | null) ?? sideToward(source, target),
      other: edge.target,
    });
    ends.push({
      edge: edge.id,
      device: edge.target,
      side: (edge.target_side as Side | null) ?? sideToward(target, source),
      other: edge.source,
    });
  }

  // 同じ辺に何本も来るときは、相手の並び順で引き出し口を割り当てる。
  // 出てくる順のまま割り当てると、線どうしが交差する
  const groups = new Map<string, typeof ends>();
  for (const end of ends) {
    const key = `${end.device}:${end.side}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(end);
  }

  /** 引き出し口のちょうど真ん中。SLOTS は 0.5 を含む奇数個で持つこと。 */
  const CENTER = (SLOTS.length - 1) / 2;

  const slotFor = (index: number, count: number): number => {
    if (count <= 1) return CENTER; // 1本なら中央から出す
    // 相手が左にいるものから順に、左の引き出し口へ
    return Math.min(SLOTS.length - 1, Math.round((index / (count - 1)) * (SLOTS.length - 1)));
  };

  const assigned = new Map<string, string>(); // 辺 + 機器 → 引き出し口
  for (const [, members] of groups) {
    const side = members[0].side;
    const along = (id: number) => {
      const node = nodes.get(id);
      if (!node) return 0;
      return side === "top" || side === "bottom" ? node.x : node.y;
    };
    members.sort((a, b) => along(a.other) - along(b.other) || a.edge - b.edge);
    members.forEach((end, index) => {
      assigned.set(`${end.edge}:${end.device}`, anchorId(end.side, slotFor(index, members.length)));
    });
  }

  for (const edge of graph.edges) {
    const source = assigned.get(`${edge.id}:${edge.source}`);
    const target = assigned.get(`${edge.id}:${edge.target}`);
    if (source && target) out.set(edge.id, { source, target });
  }

  // 1つの辺に何本来ているか。多い機器は箱を広げて間隔を確保する
  const busiest = new Map<number, number>();
  for (const [key, members] of groups) {
    const device = Number(key.split(":")[0]);
    busiest.set(device, Math.max(busiest.get(device) ?? 0, members.length));
  }

  // 同じところから何本も出るときの通り道。基板の配線と同じで、
  // 線を1本ずつ別の高さ（または横位置）に通すと重ならない。
  // 引き出し口と同じ順（相手の並び）で振らないと交差する。
  // 「近い相手から」だと、ハブが列の途中にいるときに順が入れ替わる。
  const lanes = new Map<number, number>();
  const bundles = [...groups.values()]
    .filter((members) => members.length >= 2)
    .sort((a, b) => b.length - a.length);
  for (const members of bundles) {
    const side = members[0].side;
    const vertical = side === "top" || side === "bottom";
    const along = (id: number) => {
      const node = nodes.get(id);
      if (!node) return 0;
      return vertical ? node.x : node.y;
    };
    // 通り道は辺と垂直な隙間に置く。左右から出すなら箱の間の横幅。
    const separation = (end: (typeof members)[number]) => {
      const here = nodes.get(end.device);
      const there = nodes.get(end.other);
      if (!here || !there) return 0;
      return vertical ? Math.abs(there.y - here.y) : Math.abs(there.x - here.x);
    };
    // 通り道は折れ点をずらして作るので、相手までの半分までしか使えない。
    // はみ出すと線がいったん行き過ぎてから戻り、その戻りが交差に見える。
    const closest = Math.min(...members.map(separation));
    const room = Math.max(0, closest / 2 - 20);
    const steps = Math.max(1, members.length - 1);
    const fitMin = 8 * steps;
    const gap =
      room >= fitMin ? Math.min(LANE_GAP, room / steps) : Math.max(0, room / steps);

    [...members]
      .sort((a, b) => along(a.other) - along(b.other) || a.edge - b.edge)
      .forEach((end, index) => {
        if (!lanes.has(end.edge)) lanes.set(end.edge, index * gap);
      });
  }

  return { anchors: out, busiest, lanes };
}

/** 1つの辺に来る本数から、箱の幅を決める。既定は 190px。
 *
 * 引き出し口が詰まるとポート名も重なって読めなくなるので、
 * 4本を超えたら1本あたり 55px 見込んで箱を広げる。 */
function widthFor(count: number): number | undefined {
  if (count <= 3) return undefined;
  return Math.min(620, DEFAULT_WIDTH + (count - 3) * 55);
}

/** 箱の既定の幅（styles.css の .device-node と合わせること）。 */
const DEFAULT_WIDTH = 190;

/**
 * 広げた箱を、広げる前の中心に置き直すためのずれ。
 *
 * 位置は箱の左上で持っている。幅だけ広げると中心が右へ動くので、
 * 真下にいる相手へ引いた線が斜めに折れる。図の背骨にあたる縦の線が
 * 曲がると、それだけで雑に見える。左へ半分戻して中心を揃える。
 *
 * 見た目だけの調整なので、保存するときは足し戻して元の値に返す。
 * そうしないと、保存するたびに機器が少しずつ左へ寄っていく。
 */
export function shiftFor(width: number | undefined): number {
  return width ? (width - DEFAULT_WIDTH) / 2 : 0;
}

/**
 * 画面の座標を、保存する左上へ戻す。
 *
 * ドラッグは見た目（ずらしたあとの箱）をマス目へ吸着する。ずれはマス目の
 * 整数倍ではないので、足してから丸めると保存値が別のマスへ飛び、読み込み
 * で箱がずれる。先に見た目をマス目へ乗せ、そのあとずれを足す。
 */
export function storedFromDisplay(
  display: { x: number; y: number },
  shift: number,
): { x: number; y: number } {
  return {
    x: snapToGrid(display.x) + shift,
    y: snapToGrid(display.y),
  };
}

function toFlow(
  graph: TopologyGraph,
  simple: boolean,
  options: LabelOptions,
): {
  nodes: Node<DeviceNodeData>[];
  edges: Edge[];
} {
  const { anchors, busiest, lanes } = assignAnchors(graph);

  const nodes = graph.nodes.map((node) => {
    const width = widthFor(busiest.get(node.device_id) ?? 0);
    const shift = shiftFor(width);
    return {
    id: String(node.device_id),
    type: "device",
    position: { x: node.x - shift, y: node.y },
    data: {
      hostname: node.hostname,
      vendor: simple ? "" : node.vendor,
      model: simple ? "" : node.model,
      deviceType: node.device_type,
      severity: simple ? null : node.severity,
      siteName: simple ? null : node.site_name,
      mgmtIp: simple ? null : node.mgmt_ip,
      interfaceCount: simple ? 0 : node.interface_count,
      // 番号だけの表示にしたときに、何のポートかを機器側で読ませる
      portKinds: simple ? [] : node.port_kinds,
      editable: options.editable,
      outside: node.outside,
      hiddenPeers: node.hidden_peers,
      // 線が多い機器は箱を広げる。引き出し口が詰まると線が重なって読めない
      width,
      shift,
    } satisfies DeviceNodeData,
    draggable: options.editable,
    };
  });

  const edges = graph.edges.map((edge) => ({
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    // 引き出し口は位置関係から選ぶ（人が指定していればそちら）
    sourceHandle: `s-${anchors.get(edge.id)?.source ?? "bottom-1"}`,
    targetHandle: `t-${anchors.get(edge.id)?.target ?? "top-1"}`,
    // ポート名を両端に置くため、独自のエッジで描く
    type: "port",
    animated: false,
    // 編集中は端をつまんで別の機器へ持っていける。引き直すより早い
    reconnectable: options.editable,
    className:
      `topo-edge ${edge.origin} ${edge.confirmed_both_ways || simple ? "confirmed" : ""}` +
      // 許可漏れ・ネイティブ不一致は、図の上で目立たせないと気づけない
      (!simple && (edge.vlan_mismatch.length > 0 || edge.native_mismatch) ? " mismatch" : ""),
    data: {
      sourceInterface: edge.source_interface,
      targetInterface: edge.target_interface,
      manualLabel: edge.label,
      portStyle: options.portStyle,
      alongLine: options.alongLine,
      hideLabels: false,
      // 同じところから出る線を、1本ずつ別の通り道へずらす
      lane: lanes.get(edge.id) ?? 0,
      warning: simple ? null : warningOf(edge),
    } satisfies PortEdgeData,
  }));

  return { nodes, edges };
}

/**
 * 表示の切り替えで、人が動かした位置をサーバの古い値で上書きしない。
 *
 * 編集のオンオフやポート名の出し方を変えるたびに、取得済みのグラフから
 * ノードを作り直していた。未保存の移動がそこで消え、「突然ロールバックした」
 * ように見える。同じ機器が並んでいるときは、今の座標を残す。
 */
export function keepLocalPositions<T extends { id: string; position: { x: number; y: number } }>(
  incoming: T[],
  current: T[],
  keep: boolean,
): T[] {
  if (!keep || current.length === 0) return incoming;
  // 台数が変わっても、残っている機器の未保存位置は捨てない。
  // 結線削除で L2/L3 の顔ぶれが変わったとき、配置までリセットされないようにする。
  const prev = new Map(current.map((n) => [n.id, n.position]));
  return incoming.map((n) => {
    const position = prev.get(n.id);
    return position ? { ...n, position } : n;
  });
}

function warningOf(edge: TopologyEdge): string | null {
  const warnings: string[] = [];
  if (edge.native_mismatch) warnings.push("ネイティブ不一致");
  if (edge.vlan_mismatch.length > 0) {
    warnings.push(`片側のみ VLAN ${edge.vlan_mismatch.slice(0, 4).join(",")}`);
  }
  return warnings.length ? warnings.join(" / ") : null;
}

const SIDE_LABEL: Record<string, string> = {
  auto: "自動",
  top: "上",
  bottom: "下",
  left: "左",
  right: "右",
};

/** 線をどの辺から出すかを選ぶ。自動に戻せることが大事（間違えても直せる）。 */
function SidePicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string | null;
  onChange: (side: string | null) => void;
}) {
  return (
    <label className="vlan-chip">
      {label}{" "}
      <select
        value={value ?? "auto"}
        onChange={(event) => onChange(event.target.value === "auto" ? null : event.target.value)}
      >
        {Object.entries(SIDE_LABEL).map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function nodeName(graph: TopologyGraph | undefined, deviceId: number): string {
  return graph?.nodes.find((n) => n.device_id === deviceId)?.hostname ?? `#${deviceId}`;
}

function TopologyEditor() {
  const queryClient = useQueryClient();
  // 他の画面から「この拠点を図で見る」で飛んで来られるようにする
  const [params, setParams] = useSearchParams();
  const [layer, setLayer] = useState("physical");
  const view = loadView();
  // 選んだ VLAN を持つ機器だけを残す。空なら全部表示
  const [selectedVlans, setSelectedVlans] = useState<number[]>(() =>
    params.get("vlan") ? [Number(params.get("vlan"))] : [],
  );
  const [selectedVrf, setSelectedVrf] = useState<string>(() => params.get("vrf") ?? "");
  // 拠点で絞る。現場では自分の拠点だけを見たいことが多い
  const [selectedSite, setSelectedSite] = useState<number | "">(() =>
    params.get("site") ? Number(params.get("site")) : "",
  );
  // 機器名・拠点名で探す。拠点が数百を超えると一覧から選ぶのは無理がある
  const [search, setSearch] = useState(() => params.get("q") ?? "");
  const [appliedSearch, setAppliedSearch] = useState(search);
  // 絞ったときにどこまで出すか。既定は対向まで（拠点だけだと線が1本も出ない）
  const [scope, setScope] = useState<"peers" | "site">("peers");
  // 絞らずに全体を出すのは、台数が少ないときだけ既定にする
  // VLAN が数百ある現場ではチェックボックスを全部出しても選べない
  const [vlanFilter, setVlanFilter] = useState("");
  // 機器名と線だけの表示。現場で使う図では付帯情報がむしろ邪魔になる
  const [simple, setSimple] = useState(view.simple);
  // ポート名の出し方。長い名前が線に重なると図が読めなくなる
  const [portStyle, setPortStyle] = useState<PortStyle>(view.portStyle);
  const [alongLine, setAlongLine] = useState(view.alongLine);
  const [orientation, setOrientation] = useState(view.orientation);
  // 既定は編集不可。開いた直後にうっかり動かしてしまうのを防ぐ
  const [editable, setEditable] = useState(false);
  // 図の空きをクリックして機器の枠を置く。台帳画面へ往復しないと図が作れないのを避ける
  const [placing, setPlacing] = useState(false);
  const [placeHostname, setPlaceHostname] = useState("");
  const [placeType, setPlaceType] = useState<DeviceType>("switch");
  // 再描画のきっかけ。取得結果が同じ内容だと参照が変わらず、
  // 動かした位置が画面に残ってしまうため、明示的に作り直す合図を持つ
  const [revision, setRevision] = useState(0);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<DeviceNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [dirty, setDirty] = useState(false);
  useUnsavedWarning(dirty);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const instance = useRef<ReactFlowInstance<Node<DeviceNodeData>, Edge> | null>(null);
  // ドラッグの過程。保存は基準から動いた機器だけ送る
  const historyRef = useRef<EditHistory | null>(null);
  const [historyTick, setHistoryTick] = useState(0);
  // 保存・リセット・整頓のあとは、取り直した配置をそのまま使う
  const takeServerLayout = useRef(false);
  // 取り消しで当て直すときは、過程に足さない
  const applyingHistory = useRef(false);
  // 図を開いたとき・拠点を変えたときだけ全体を収める。
  // 保存のたびに fitView すると、置いた位置がリセットされたように見える
  const shouldFit = useRef(true);

  const scoped = selectedSite !== "" || appliedSearch.trim() !== "";
  const { data: stats } = useQuery({
    queryKey: ["topologyStats", layer],
    queryFn: () => api.getTopologyStats(layer),
  });
  // 全体図は台数が増えると読めないし、描くだけで重い。
  // 絞っていないときは、物理だけ自動で止める（L2/L3/論理は中身が絞られる）
  const tooWide = !scoped && layer === "physical" && (stats?.device_count ?? 0) > WIDE_LIMIT;

  const { data: graph, isLoading } = useQuery({
    queryKey: ["topology", layer, orientation, selectedSite, appliedSearch, scope],
    queryFn: () =>
      api.getTopology(layer, false, orientation, {
        site_id: selectedSite === "" ? undefined : selectedSite,
        q: appliedSearch.trim() || undefined,
        scope,
      }),
    enabled: !tooWide,
  });
  const { data: tunnelInfo } = useQuery({
    queryKey: ["topologyTunnels"],
    queryFn: () => api.getTopologyTunnels(),
    enabled: layer === "logical",
  });
  const { data: vlans } = useQuery({
    queryKey: ["topologyVlans"],
    queryFn: () => api.listTopologyVlans(),
  });
  const { data: vrfs } = useQuery({
    queryKey: ["topologyVrfs"],
    queryFn: () => api.listTopologyVrfs(),
  });
  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: () => api.listSites() });

  // 物理の全体図が出せない規模なら、最初から本社を選ぶ。
  // 「表示できる機器がありません」だけのデモにはしない。
  useEffect(() => {
    if (selectedSite !== "") return;
    if (params.get("site")) return;
    if (layer !== "physical") return;
    if (!sites?.length || !stats) return;
    if (stats.device_count <= WIDE_LIMIT) return;
    const hq = sites.find((site) => site.name.includes("本社")) ?? sites[0];
    setSelectedSite(hq.id);
  }, [sites, stats, selectedSite, layer, params]);

  useEffect(() => {
    if (!graph) return;
    const flow = toFlow(graph, simple, { portStyle, alongLine, editable });

    // VLAN / VRF / 拠点を選んでいるときは、該当しない機器を薄くする。
    // 消さずに残すのは、選んだセグメントが「どこまで伸びていないか」も
    // 同時に見えた方が構成を掴みやすいため。
    const filtering = selectedVlans.length > 0 || selectedVrf !== "" || selectedSite !== "";
    if (filtering) {
      const matches = new Set(
        graph.nodes
          .filter(
            (n) =>
              (selectedVlans.length === 0 || n.vlans.some((v) => selectedVlans.includes(v))) &&
              (selectedVrf === "" || n.vrfs.includes(selectedVrf)) &&
              (selectedSite === "" || n.site_id === selectedSite),
          )
          .map((n) => String(n.device_id)),
      );

      // 対象と直接つながっている相手は、薄くしすぎない。
      // 拠点で絞ったときに上位が消えて見えると、「どこにも繋がっていない」図になる
      const neighbours = new Set<string>();
      graph.edges.forEach((e) => {
        const source = String(e.source);
        const target = String(e.target);
        if (matches.has(source) && !matches.has(target)) neighbours.add(target);
        if (matches.has(target) && !matches.has(source)) neighbours.add(source);
      });

      flow.nodes.forEach((n) => {
        if (matches.has(n.id)) return;
        n.style = { ...(n.style ?? {}), opacity: neighbours.has(n.id) ? 0.55 : 0.15 };
      });
      flow.edges.forEach((e) => {
        const source = String(e.source);
        const target = String(e.target);
        if (matches.has(source) && matches.has(target)) return;
        // 対象から出ている線は残す。その先に何があるかが分からないと構成が読めない
        const boundary = matches.has(source) || matches.has(target);
        e.style = { ...(e.style ?? {}), opacity: boundary ? 0.5 : 0.1 };
      });
    }

    const keepLocal = dirty && !takeServerLayout.current;
    takeServerLayout.current = false;
    let nextNodes = flow.nodes;
    setNodes((current) => {
      nextNodes = keepLocalPositions(flow.nodes, current, keepLocal);
      return nextNodes;
    });
    if (!keepLocal) {
      historyRef.current = startHistory(capturePositions(nextNodes));
      setHistoryTick((value) => value + 1);
    }
    setEdges(flow.edges);
    // 未保存の移動は消さない。以前はここで dirty を落としていたため、
    // 表示を切り替えただけで「保存済み」に見え、位置もサーバの古い値に戻った
  }, [
    graph,
    selectedVlans,
    selectedVrf,
    selectedSite,
    simple,
    portStyle,
    alongLine,
    editable,
    revision,
    dirty,
    setNodes,
    setEdges,
  ]);

  useEffect(() => {
    shouldFit.current = true;
  }, [layer, selectedSite, appliedSearch, orientation, scope]);

  useEffect(() => {
    if (!graph || !instance.current || !shouldFit.current) return;
    instance.current.fitView({ padding: 0.2 });
    shouldFit.current = false;
  }, [graph, nodes.length]);

  // 選んだ表示を覚えておく。線に沿わせるかどうかは好みが分かれるところなので、
  // 毎回チェックし直さずに済むようにする
  useEffect(() => {
    localStorage.setItem(VIEW_KEY, JSON.stringify({ portStyle, alongLine, simple, orientation }));
  }, [portStyle, alongLine, simple, orientation]);

  // 絞り込みを URL に残す。この画面をそのまま人に送れる
  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedSite !== "") next.set("site", String(selectedSite));
    if (appliedSearch.trim()) next.set("q", appliedSearch.trim());
    if (selectedVrf) next.set("vrf", selectedVrf);
    if (selectedVlans.length === 1) next.set("vlan", String(selectedVlans[0]));
    setParams(next, { replace: true });
  }, [selectedSite, appliedSearch, selectedVrf, selectedVlans, setParams]);

  const refresh = async ({ takeLayout = true }: { takeLayout?: boolean } = {}) => {
    // 取り直してから画面を作り直す。以前はキャッシュの古いグラフで
    // 先に描いていたため、保存・リセットの直後に配置が戻って見えた
    // 結線の追加・削除では、未保存の配置を消さない（takeLayout: false）
    takeServerLayout.current = takeLayout;
    await queryClient.refetchQueries({ queryKey: ["topology"] });
    queryClient.invalidateQueries({ queryKey: ["topologyStats"] });
    // 取得結果が前と同じ内容だと参照が変わらず、画面が作り直されない。
    // 「リセットしたのに戻らない」のはこれが原因なので、合図を出しておく
    setRevision((value) => value + 1);
  };

  const save = useMutation({
    mutationFn: () => {
      const history = historyRef.current;
      const moved = new Set(history ? changedIds(history) : []);
      const byId = new Map((graph?.nodes ?? []).map((n) => [String(n.device_id), n]));
      return api.savePositions(
        layer,
        nodes
          .filter((n) => moved.has(n.id))
          .filter((n) => byId.get(n.id)?.outside !== true)
          .map((n) => {
            const stored = storedFromDisplay(n.position, n.data.shift ?? 0);
            return {
              device_id: Number(n.id),
              x: stored.x,
              y: stored.y,
            };
          }),
      );
    },
    onSuccess: async () => {
      await refresh();
      setDirty(false);
      setNotice("配置を保存しました");
    },
  });

  /** いま見えている範囲。リセットも整列もこの範囲だけに効かせる */
  const viewScope = {
    site_id: selectedSite === "" ? undefined : selectedSite,
    q: appliedSearch.trim() || undefined,
  };

  const reset = useMutation({
    mutationFn: () => api.resetPositions(layer, viewScope),
    onSuccess: async (result) => {
      shouldFit.current = true;
      await refresh();
      setDirty(false);
      setNotice(
        `配置をリセットしました（${result.reset} 台を自動レイアウトに戻しました）` +
          (viewScope.site_id || viewScope.q ? "。絞り込みの外は触っていません" : ""),
      );
    },
  });

  const arrange = useMutation({
    mutationFn: () =>
      api.arrangePositions(layer, {
        ...viewScope,
        scope,
        orientation,
      }),
    onSuccess: async (result) => {
      shouldFit.current = true;
      await refresh();
      setDirty(false);
      setNotice(
        result.arranged
          ? `配置を整えました（マス目・揃い。${result.arranged} 台）`
          : "配置を整えました（動かす機器がありません）",
      );
    },
  });

  const rebuild = useMutation({
    mutationFn: api.rebuildTopology,
    onSuccess: (result) => {
      const unresolved = result.unresolved_neighbors.length;
      setNotice(
        `Config から ${result.links_created} 本の結線を作り直しました` +
          (unresolved ? `（台帳に無い隣接先が ${unresolved} 件あります）` : ""),
      );
      refresh();
    },
  });

  const createLink = useMutation({
    mutationFn: (connection: Connection) =>
      api.createLink({
        a_device_id: Number(connection.source),
        b_device_id: Number(connection.target),
      }),
    onSuccess: async () => {
      setNotice("結線を追加しました（すぐ保存されます）");
      await refresh({ takeLayout: false });
    },
    onError: () => {
      // 失敗した仮の線は残さない。次の取り直しでも消えるが、すぐ戻す
      void refresh({ takeLayout: false });
    },
  });

  const placeDevice = useMutation({
    mutationFn: async (input: {
      x: number;
      y: number;
      hostname: string;
      device_type: DeviceType;
      site_id: number | null;
    }) => {
      const device = await api.createDevice({
        hostname: input.hostname,
        vendor: "未記入",
        model: "未記入",
        device_type: input.device_type,
        status: "active",
        ...(input.site_id != null ? { site_id: input.site_id } : {}),
      });
      await api.savePositions(layer, [{ device_id: device.id, x: input.x, y: input.y }]);
      return device;
    },
    onSuccess: async (device) => {
      setPlaceHostname("");
      setNotice(
        `${device.hostname} を図に置きました。箱のハンドルをドラッグすると線を引けます`,
      );
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      await refresh({ takeLayout: false });
    },
  });

  const selectedEdgeData = selectedEdge
    ? graph?.edges.find((e) => String(e.id) === selectedEdge.id)
    : undefined;

  const updateSide = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: number;
      body: { a_side?: string | null; b_side?: string | null };
    }) => api.updateLink(id, body),
    onSuccess: () => {
      setNotice("線の引き出し位置を変えました");
      void refresh({ takeLayout: false });
    },
  });

  const removeLink = useMutation({
    mutationFn: (id: number) => api.deleteLink(id),
    onSuccess: () => {
      setSelectedEdge(null);
      setNotice("結線を削除しました");
      void refresh({ takeLayout: false });
    },
  });

  const rememberHistory = useCallback((next: EditHistory) => {
    historyRef.current = next;
    setDirty(historyIsDirty(next));
    setHistoryTick((value) => value + 1);
  }, []);

  const discardEdits = useCallback(() => {
    const history = historyRef.current;
    if (!history) return;
    applyingHistory.current = true;
    setNodes((current) => applyPositions(current, history.baseline));
    applyingHistory.current = false;
    rememberHistory(startHistory(history.baseline));
    setNotice("編集中の配置を破棄しました");
  }, [rememberHistory, setNodes]);

  const undoEdit = useCallback(() => {
    const history = historyRef.current;
    if (!history) return;
    const next = undoHistory(history);
    if (!next) return;
    applyingHistory.current = true;
    setNodes((current) => applyPositions(current, currentPositions(next)));
    applyingHistory.current = false;
    rememberHistory(next);
  }, [rememberHistory, setNodes]);

  const redoEdit = useCallback(() => {
    const history = historyRef.current;
    if (!history) return;
    const next = redoHistory(history);
    if (!next) return;
    applyingHistory.current = true;
    setNodes((current) => applyPositions(current, currentPositions(next)));
    applyingHistory.current = false;
    rememberHistory(next);
  }, [rememberHistory, setNodes]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<Node<DeviceNodeData>>[]) => {
      onNodesChange(changes);
      if (applyingHistory.current) return;
      // ドラッグ中の途中座標は残さない
      if (changes.some((change) => change.type === "position" && change.dragging === true)) {
        return;
      }
      // ドラッグ終了・矢印キーなど、確定した移動（dragging: false）
      const settled = changes.some(
        (change) => change.type === "position" && change.dragging === false,
      );
      if (!settled) return;
      requestAnimationFrame(() => {
        if (applyingHistory.current) return;
        const current = instance.current?.getNodes() ?? [];
        if (current.length === 0) return;
        const history = historyRef.current ?? startHistory(capturePositions(current));
        rememberHistory(pushStep(history, capturePositions(current)));
      });
    },
    [onNodesChange, rememberHistory],
  );

  // 入力欄にいるときはショートカットを奪わない
  useEffect(() => {
    if (!editable) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      const withMod = event.metaKey || event.ctrlKey;
      if (!withMod) return;
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        undoEdit();
      } else if ((key === "z" && event.shiftKey) || key === "y") {
        event.preventDefault();
        redoEdit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editable, undoEdit, redoEdit]);

  /** 線の端を別の機器へ付け替える。どちらの端が動いたかは、変わった側で判る */
  const reconnect = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, number> }) =>
      api.updateLink(id, body),
    onSuccess: () => {
      setNotice("結線をつなぎ替えました");
      void refresh({ takeLayout: false });
    },
  });

  const onReconnect = useCallback(
    (oldEdge: Edge, connection: Connection) => {
      if (!editable) return;
      if (connection.source === connection.target) return;

      const body: Record<string, number> = {};
      if (connection.source !== oldEdge.source) body.a_device_id = Number(connection.source);
      if (connection.target !== oldEdge.target) body.b_device_id = Number(connection.target);
      if (!Object.keys(body).length) return;

      reconnect.mutate({ id: Number(oldEdge.id), body });
    },
    [editable, reconnect],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      // ロック中は React Flow 側でも掴めないようにしてあるが、ここでも止める。
      // 結線は保存が走るので、見た目の制御だけに任せない
      if (!editable) return;
      if (layer === "logical") {
        setNotice(
          "論理構成では手で線を引けません。トンネルは Config から取り込みます。物理／L2／L3 で結線してください",
        );
        return;
      }
      if (connection.source === connection.target) return;
      setEdges((current) =>
        addEdge({ ...connection, type: "smoothstep", className: "topo-edge manual" }, current),
      );
      createLink.mutate(connection);
    },
    [createLink, editable, layer, setEdges],
  );

  const onPaneClick = useCallback(
    (event: MouseEvent) => {
      setSelectedEdge(null);
      if (!placing || !editable) return;
      if (layer !== "physical") {
        setNotice("機器の枠は物理構成で置きます。L2 / L3 / 論理では線の無い箱は出ません");
        return;
      }
      const hostname = placeHostname.trim();
      if (!hostname) {
        setNotice("先に機器名を入れてから、図の空きをクリックしてください");
        return;
      }
      const flow = instance.current;
      if (!flow || placeDevice.isPending) return;
      const point = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const position = placementFromClick(point.x, point.y);
      placeDevice.mutate({
        ...position,
        hostname,
        device_type: placeType,
        site_id: selectedSite === "" ? null : selectedSite,
      });
    },
    [editable, layer, placeDevice, placeHostname, placeType, placing, selectedSite],
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      if (!editable) return;
      // 論理図の線はトンネルID。物理の結線APIに渡すと別の線を消す
      if (layer === "logical") {
        setNotice("論理構成の線はトンネルです。ここでは消せません");
        void refresh({ takeLayout: false });
        return;
      }
      for (const edge of deleted) {
        const id = Number(edge.id);
        if (Number.isFinite(id)) removeLink.mutate(id);
      }
    },
    [editable, layer, refresh, removeLink],
  );

  /** 選択中のものは常に出す。消えると外せなくなるため。 */
  const shownVlans = useMemo(() => {
    const keyword = vlanFilter.trim().toLowerCase();
    const matched = (vlans ?? []).filter(
      (vlan) =>
        selectedVlans.includes(vlan.vlan_id) ||
        !keyword ||
        String(vlan.vlan_id).includes(keyword) ||
        (vlan.name ?? "").toLowerCase().includes(keyword) ||
        (vlan.purpose ?? "").toLowerCase().includes(keyword),
    );
    return keyword ? matched : matched.slice(0, 60);
  }, [vlans, vlanFilter, selectedVlans]);

  /** 実際に使われている種別だけ凡例に出す。使っていない色を並べても読み手が困る。 */
  const usedMedia = useMemo(() => {
    const found = new Set<Media>();
    for (const edge of graph?.edges ?? []) {
      for (const name of [edge.source_interface, edge.target_interface]) {
        if (name) found.add(mediaOf(name));
      }
    }
    const order: Media[] = Object.keys(MEDIA_LABEL) as Media[];
    return order.filter((media) => found.has(media));
  }, [graph]);

  const legend = useMemo(
    () => [
      { className: "topo-edge discovered confirmed", text: "自動推定（両側で確認）" },
      { className: "topo-edge discovered", text: "自動推定（片側のみ）" },
      { className: "topo-edge manual", text: "手動で追加" },
    ],
    [],
  );

  // historyTick で再描画し、ref の過程をボタンの有効／無効に反映する
  const undoable = historyTick >= 0 && !!historyRef.current && canUndo(historyRef.current);
  const redoable = historyTick >= 0 && !!historyRef.current && canRedo(historyRef.current);

  return (
    <>
      <h1>トポロジ図</h1>
      <p className="subtitle">
        Config の LLDP / CDP から結線を推定して描きます。
        {editable ? (
          <>
            <strong>編集中です。</strong>
            機器をドラッグして配置を整え、機器同士をつなげば手で線を引けます。
            「機器を置く」を押して名前を入れ、図の空きをクリックすると枠が置かれます。
            線はすぐ保存されます。配置だけ「配置を保存」が必要です。
            線を選ぶと、図の左上に削除などの短い操作が出ます（Delete でも消えます）。
            途中の配置は「一つ戻す」「やり直す」「破棄」で行き来できます。
          </>
        ) : (
          <>図は動かせません。並べ替えたいときは「編集する」を押してください。マス目に合わせて置けます。</>
        )}
        <br />
        {LAYERS.find((item) => item.value === layer)?.hint}
      </p>

      <div className="toolbar">
        <button
          className={editable ? "primary" : ""}
          onClick={() => {
            if (editable && dirty) {
              if (!confirm("編集中の配置を破棄して編集を終えますか？")) return;
              discardEdits();
            }
            setEditable((value) => !value);
            setPlacing(false);
          }}
          title={
            editable
              ? "編集を終える。未保存の配置があるときは破棄の確認を出します"
              : "機器の移動・結線の追加ができるようになります"
          }
        >
          {editable ? "🔓 編集中" : "🔒 編集する"}
        </button>
        <select
          value={layer}
          title={LAYERS.find((item) => item.value === layer)?.hint}
          onChange={(e) => {
            if (!confirmLeave(dirty)) return;
            setDirty(false);
            setPlacing(false);
            setLayer(e.target.value);
          }}
        >
          {LAYERS.map((item) => (
            <option key={item.value} value={item.value} title={item.hint}>
              {item.label}
            </option>
          ))}
        </select>
        <select
          value={selectedSite}
          onChange={(e) => setSelectedSite(e.target.value === "" ? "" : Number(e.target.value))}
          title="拠点で絞り込みます。書き出すとその拠点だけの構成図になります"
        >
          <option value="">拠点: すべて</option>
          {(sites ?? []).map((site) => (
            <option key={site.id} value={site.id}>
              拠点: {site.name}
            </option>
          ))}
        </select>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setAppliedSearch(search);
          }}
          style={{ display: "contents" }}
        >
          <input
            type="search"
            value={search}
            placeholder="拠点名・機器名で探す"
            onChange={(event) => setSearch(event.target.value)}
            onBlur={() => setAppliedSearch(search)}
            title="拠点名・機器名・管理IPで探します。全国規模になると一覧からは選べません"
            style={{ minWidth: 180 }}
          />
          {appliedSearch && (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                setAppliedSearch("");
              }}
            >
              検索を消す
            </button>
          )}
        </form>
        <select
          value={scope}
          onChange={(event) => setScope(event.target.value as "peers" | "site")}
          title="絞ったときに、その先（対向）まで出すかどうか"
        >
          <option value="peers">範囲: 対向まで</option>
          <option value="site">範囲: 絞った分だけ</option>
        </select>
        {editable && (
          <>
            <button
              onClick={undoEdit}
              disabled={!undoable}
              title="一つ前の配置に戻します（Ctrl+Z）"
            >
              一つ戻す
            </button>
            <button
              onClick={redoEdit}
              disabled={!redoable}
              title="取り消した配置を戻します（Ctrl+Shift+Z）"
            >
              やり直す
            </button>
            <button
              className="primary"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
              title="編集した配置をサーバに残します"
            >
              {dirty ? "配置を保存" : "保存済み"}
            </button>
            <button
              disabled={!dirty}
              onClick={() => {
                if (!confirm("編集中の配置を破棄して、保存前の状態に戻しますか？")) return;
                discardEdits();
              }}
              title="保存していない配置を捨てて、編集を始める前に戻します"
            >
              破棄
            </button>
            <button
              onClick={() => rebuild.mutate()}
              disabled={rebuild.isPending}
            >
              Config から結線を作り直す
            </button>
            {layer === "physical" && (
              <>
                <button
                  className={placing ? "primary" : ""}
                  onClick={() => {
                    if (tooWide) {
                      setNotice("全体図では置けません。拠点を選んでから機器を置いてください");
                      return;
                    }
                    setPlacing((value) => !value);
                  }}
                  title="名前を入れて図の空きをクリックすると、機器の枠を置けます"
                >
                  {placing ? "置くのをやめる" : "機器を置く"}
                </button>
                {placing && (
                  <>
                    <input
                      type="text"
                      value={placeHostname}
                      placeholder="機器名"
                      onChange={(event) => setPlaceHostname(event.target.value)}
                      title="図に出すホスト名"
                      style={{ minWidth: 140 }}
                    />
                    <select
                      value={placeType}
                      onChange={(event) => setPlaceType(event.target.value as DeviceType)}
                      title="機器の種別"
                    >
                      {(Object.keys(DEVICE_TYPE_LABEL) as DeviceType[]).map((type) => (
                        <option key={type} value={type}>
                          {DEVICE_TYPE_LABEL[type]}
                        </option>
                      ))}
                    </select>
                  </>
                )}
              </>
            )}
          </>
        )}
        {/* 人が置いた位置を起点に、マス目・揃い・間隔だけ整える。木に組み直すのはリセット */}
        <button
          className="primary"
          onClick={() => arrange.mutate()}
          disabled={arrange.isPending}
          title="マス目に乗せ、近い段と列を揃えます。大きく動かしすぎないよう、空けられない重なりは触りません。初期配置へ戻すのはリセットです"
        >
          {arrange.isPending ? "整えています…" : "配置を整える"}
        </button>
        {/* リセットは編集中でなくても要る。ロックしたまま「元に戻したい」が普通 */}
        <button
          onClick={() => {
            const where =
              viewScope.site_id || viewScope.q ? "いま絞り込んでいる範囲の配置" : "すべての配置";
            if (confirm(`${where}をリセットして自動レイアウトに戻しますか？`)) reset.mutate();
          }}
        >
          配置をリセット
        </button>
        <button onClick={() => instance.current?.fitView({ padding: 0.2 })}>全体を表示</button>
        <label className="vlan-chip" title="機器名と結線だけの表示に切り替えます">
          <input type="checkbox" checked={simple} onChange={(e) => setSimple(e.target.checked)} />{" "}
          シンプル表示
        </label>
        <select
          value={orientation}
          onChange={(e) => setOrientation(e.target.value as "vertical" | "horizontal")}
          title="自動配置の向きを変えます。人が動かした配置はそのまま残ります"
        >
          <option value="vertical">並び: 上下</option>
          <option value="horizontal">並び: 左右</option>
        </select>
        <select
          value={portStyle}
          onChange={(e) => setPortStyle(e.target.value as PortStyle)}
          title={PORT_STYLES.find((item) => item.value === portStyle)?.hint}
        >
          {PORT_STYLES.map((item) => (
            <option key={item.value} value={item.value}>
              ポート名: {item.label}
            </option>
          ))}
        </select>
        <label className="vlan-chip" title="ポート名を線の向きに合わせて回転します">
          <input
            type="checkbox"
            checked={alongLine}
            onChange={(e) => setAlongLine(e.target.checked)}
          />{" "}
          線に沿わせる
        </label>
        <a
          className="button"
          href={api.topologyExportUrl(layer, {
            vlan: selectedVlans.length === 1 ? selectedVlans[0] : undefined,
            vrf: selectedVrf || undefined,
            site_id: selectedSite === "" ? undefined : selectedSite,
            style: simple ? "simple" : undefined,
            port_style: portStyle,
            along_line: alongLine ? "true" : undefined,
            orientation,
          })}
          title="設計書や報告書に貼れるベクタ形式で書き出します（Word / PowerPoint にそのまま挿入できます）"
        >
          図をファイルに出す（SVG{simple ? " / シンプル" : ""}）
        </a>
        {/* Excel はまとめて1つ。ボタンを分けるより、開けば全部入っている方が配りやすい */}
        <a
          className="button"
          href={api.workbookUrl(layer, {
            site_id: selectedSite === "" ? undefined : selectedSite,
            scope,
          })}
          title="構成図（セルの罫線で描いた図）・結線表・ポート一覧を1つの Excel で出します。図は手元で直せます"
        >
          Excel で出す（図＋表）
        </a>
        <RouterLink to="/configs">
          <button>Config を取り込む</button>
        </RouterLink>
      </div>

      {notice && (
        <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 12 }}>
          {notice}
        </div>
      )}
      {(save.error || createLink.error || rebuild.error || reconnect.error || placeDevice.error) && (
        <div className="error">
          {
            (
              (save.error ??
                createLink.error ??
                rebuild.error ??
                reconnect.error ??
                placeDevice.error) as Error
            ).message
          }
        </div>
      )}

      {stats && (
        <div className="cards" style={{ marginBottom: 12 }}>
          <div className="card">
            <div className="label">機器</div>
            <div className="value">{stats.device_count}</div>
          </div>
          <div className="card">
            <div className="label">結線</div>
            <div className="value">{stats.link_count}</div>
          </div>
          <div className="card">
            <div className="label">両側で確認済み</div>
            <div className="value" style={{ color: "var(--sev-ok)" }}>
              {stats.confirmed_links}
            </div>
          </div>
          <div className="card">
            <div className="label">手動で追加</div>
            <div className="value">{stats.manual_links}</div>
          </div>
          <div className="card">
            <div className="label">未接続の機器</div>
            <div className="value" style={{ color: "var(--sev-unknown)" }}>
              {stats.isolated_count.toLocaleString()}
            </div>
          </div>
        </div>
      )}

      <div className={`topo-canvas${placing ? " placing" : ""}`}>
        {tooWide ? (
          // 全国規模では全体図は読めない。拠点を選ぶか名前で探す方が早い。
          // それでも見たい人のために、出す手段は残しておく
          <div className="empty" style={{ margin: 40 }}>
            <p>
              機器が {stats?.device_count} 台あります。全体図は読めないし、描くと画面が止まるので、
              <strong>拠点を選ぶ</strong>か<strong>拠点名・機器名で探して</strong>ください。
            </p>
            <p className="muted">選んだ拠点の対向（直接つながっている上位）までは自動で出ます。</p>
            <p className="muted">
              L2 / L3 / 論理構成は、中身が絞られるので全体のままでも開けます。
            </p>
          </div>
        ) : isLoading ? (
          <p className="muted" style={{ padding: 20 }}>
            読み込み中…
          </p>
        ) : nodes.length === 0 && !(editable && layer === "physical") ? (
          <div className="empty" style={{ margin: 40 }}>
            {layer === "l2" ? (
              <>
                <p>VLAN が載っている結線がありません。</p>
                <p className="muted">
                  ポートに VLAN / トランク設定が入ると、ここに L2 の図が出ます。物理構成とは別です。
                </p>
              </>
            ) : layer === "l3" ? (
              <>
                <p>L3 の結線がありません。</p>
                <p className="muted">
                  ルータ同士の結線や、両端に IP が付いたポートがあるとここに出ます。
                </p>
              </>
            ) : layer === "logical" ? (
              <>
                <p>論理構成に出せるトンネルがありません。</p>
                <p className="muted">
                  Config から IPsec / GRE などを取り込むと、物理とは別の通り道として出ます。
                  {tunnelInfo && tunnelInfo.unresolved.length > 0 && (
                    <>
                      {" "}
                      相手が台帳に無いトンネルが {tunnelInfo.unresolved.length}{" "}
                      件あります（黙って消してはいません）。
                    </>
                  )}
                </p>
              </>
            ) : (
              <>
                表示できる機器がありません。編集中に「機器を置く」から図へ枠を置けます。台帳から
                登録しても出ます。
              </>
            )}
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            nodesDraggable={editable}
            nodesConnectable={editable && layer !== "logical"}
            elementsSelectable
            deleteKeyCode={editable && layer !== "logical" ? ["Backspace", "Delete"] : null}
            snapToGrid={editable}
            snapGrid={[LAYOUT_GRID, LAYOUT_GRID]}
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
            onBeforeDelete={async ({ nodes: deletingNodes }) => {
              // 機器の削除は台帳側。ノード選択中の Delete で、ぶら下がる線まで消さない
              if (deletingNodes.length > 0) return false;
              return true;
            }}
            onEdgesDelete={onEdgesDelete}
            onConnect={onConnect}
            onReconnect={onReconnect}
            edgesReconnectable={editable}
            onEdgeClick={(_, edge) => setSelectedEdge(edge)}
            onPaneClick={onPaneClick}
            onInit={(flow) => {
              instance.current = flow;
              if (shouldFit.current) {
                flow.fitView({ padding: 0.2 });
                shouldFit.current = false;
              }
            }}
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
          >
            <Background
              id="layout-grid"
              variant={BackgroundVariant.Lines}
              gap={LAYOUT_GRID}
              size={1}
              color="#2a3340"
            />
            {/* React Flow が持っている鍵は隠す。こちらの「編集する」と連動しておらず、
                押すと表示だけロックが外れて、二重の鍵があるように見えてしまう */}
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable className="topo-minimap" />
            {selectedEdge && selectedEdgeData && (
              <Panel position="top-left" className="topo-selection-panel">
                <div className="topo-selection-panel-body">
                  <div className="topo-selection-panel-row">
                    <span className="topo-selection-panel-label" title="選択中の結線">
                      {nodeName(graph, selectedEdgeData.source)}
                      {selectedEdgeData.source_interface
                        ? ` ${selectedEdgeData.source_interface}`
                        : ""}
                      {" — "}
                      {nodeName(graph, selectedEdgeData.target)}
                      {selectedEdgeData.target_interface
                        ? ` ${selectedEdgeData.target_interface}`
                        : ""}
                    </span>
                    {editable && layer !== "logical" ? (
                      <>
                        <button
                          className="danger"
                          onClick={() => removeLink.mutate(Number(selectedEdge.id))}
                          disabled={removeLink.isPending}
                          title="Delete / Backspace でも消えます"
                        >
                          削除
                        </button>
                        <SidePicker
                          label="出る辺"
                          value={selectedEdgeData.source_side}
                          onChange={(side) =>
                            updateSide.mutate({
                              id: selectedEdgeData.id,
                              body: { a_side: side },
                            })
                          }
                        />
                        <SidePicker
                          label="入る辺"
                          value={selectedEdgeData.target_side}
                          onChange={(side) =>
                            updateSide.mutate({
                              id: selectedEdgeData.id,
                              body: { b_side: side },
                            })
                          }
                        />
                      </>
                    ) : editable ? (
                      <span className="muted">論理構成の線はここでは消せません</span>
                    ) : (
                      <span className="muted">消すときは編集する</span>
                    )}
                    <button
                      type="button"
                      className="topo-selection-panel-close"
                      onClick={() => setSelectedEdge(null)}
                      title="選択を解除"
                    >
                      ×
                    </button>
                  </div>
                </div>
              </Panel>
            )}
          </ReactFlow>
        )}
      </div>

      <div className="toolbar" style={{ marginTop: 12 }}>
        {(vlans ?? []).length > 0 && (
          <div className="vlan-filter">
            <div className="vlan-filter-head">
              <strong>VLAN で絞り込む</strong>
              <span className="muted">
                選ぶと、その VLAN を持たない機器が薄くなります（どこまで伸びているかを見るため）
              </span>
              <input
                type="search"
                placeholder="番号・名称で絞る"
                value={vlanFilter}
                onChange={(e) => setVlanFilter(e.target.value)}
              />
              {selectedVlans.length > 0 && (
                <button onClick={() => setSelectedVlans([])}>選択を解除</button>
              )}
              <RouterLink to="/vlans">
                <button title="VLAN の名称と用途を登録します">名称・用途を編集</button>
              </RouterLink>
            </div>
            <div className="vlan-filter-list">
              {shownVlans.map((vlan) => (
                <label key={vlan.vlan_id} className="vlan-chip">
                  <input
                    type="checkbox"
                    checked={selectedVlans.includes(vlan.vlan_id)}
                    onChange={(e) =>
                      setSelectedVlans((prev) =>
                        e.target.checked
                          ? [...prev, vlan.vlan_id]
                          : prev.filter((v) => v !== vlan.vlan_id),
                      )
                    }
                  />{" "}
                  <strong>VLAN {vlan.vlan_id}</strong>
                  {vlan.name && <span> {vlan.name}</span>}
                  <span className="muted">
                    {" "}
                    / {vlan.device_ids.length}台
                    {vlan.access_port_count > 0 && ` / アクセス${vlan.access_port_count}`}
                    {vlan.trunk_port_count > 0 && ` / トランク${vlan.trunk_port_count}`}
                  </span>
                </label>
              ))}
              {shownVlans.length < (vlans ?? []).length && (
                <span className="muted">
                  ほか {(vlans ?? []).length - shownVlans.length} 件（絞り込んで表示）
                </span>
              )}
            </div>
          </div>
        )}

        {(vrfs ?? []).length > 0 && (
          <div className="vlan-filter">
            <div className="vlan-filter-head">
              <strong>VRF（仮想ルータ）で絞り込む</strong>
              <span className="muted">同じ線で繋がって見えても、VRF が違えば通信できません</span>
              <select value={selectedVrf} onChange={(e) => setSelectedVrf(e.target.value)}>
                <option value="">すべて</option>
                {(vrfs ?? []).map((vrf) => (
                  <option key={vrf.name} value={vrf.name}>
                    {vrf.name}（{vrf.device_ids.length}台）
                  </option>
                ))}
              </select>
            </div>
            <div className="vlan-filter-list">
              {(vrfs ?? []).map((vrf) => (
                <span key={vrf.name} className="vlan-chip">
                  <strong>{vrf.name}</strong>
                  {vrf.rd && <span className="muted"> RD {vrf.rd}</span>}
                  <span className="muted"> / {vrf.device_ids.length}台</span>
                  {vrf.rd_mismatch && (
                    <span
                      className="badge warning"
                      title="同じ VRF 名なのに機器ごとに RD が違います"
                    >
                      RD 不一致
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}

        {legend.map((item) => (
          <span key={item.text} className="topo-legend">
            <span className={`topo-legend-line ${item.className}`} /> {item.text}
          </span>
        ))}

        {usedMedia.length > 0 && (
          <span className="topo-legend" title="ポート名の色は種別（速度）を表します">
            ポート種別:{" "}
            {usedMedia.map((media) => (
              <strong key={media} style={{ color: MEDIA_COLOR[media], marginLeft: 8 }}>
                {MEDIA_LABEL[media]}
              </strong>
            ))}
          </span>
        )}
      </div>

      {stats && stats.isolated_count > 0 && (
        <>
          <h2>どこにも繋がっていない機器</h2>
          <p className="muted" style={{ marginTop: -6 }}>
            Config が未取り込みか、隣接情報が取れていない機器です。手で線を引くこともできます。
          </p>
          <div className="toolbar">
            {stats.isolated_devices.map((hostname) => (
              <span key={hostname} className="badge unknown">
                {hostname}
              </span>
            ))}
            {stats.isolated_count > stats.isolated_devices.length && (
              <span className="muted">
                ほか {(stats.isolated_count - stats.isolated_devices.length).toLocaleString()} 台
              </span>
            )}
          </div>
        </>
      )}
    </>
  );
}

export default function TopologyPage() {
  return (
    <ReactFlowProvider>
      <TopologyEditor />
    </ReactFlowProvider>
  );
}
