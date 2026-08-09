import {
  Background,
  Controls,
  MiniMap,
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { api } from "../api/client";
import type { TopologyEdge, TopologyGraph } from "../api/types";
import DeviceNode, { type DeviceNodeData } from "../components/DeviceNode";
import PortLabeledEdge, { type PortEdgeData } from "../components/PortLabeledEdge";
import { MEDIA_COLOR, MEDIA_LABEL, mediaOf, type Media, type PortStyle } from "../lib/portlabel";

const NODE_TYPES = { device: DeviceNode };
const EDGE_TYPES = { port: PortLabeledEdge };

const PORT_STYLES: { value: PortStyle; label: string; hint: string }[] = [
  { value: "short", label: "略記", hint: "GigabitEthernet1/0/24 → Gi1/0/24" },
  { value: "number", label: "番号だけ", hint: "種別は色で表します（Gi は青、Te は緑）" },
  { value: "full", label: "そのまま", hint: "Config に書かれている名前をそのまま出します" },
];

const LAYERS = [
  { value: "physical", label: "物理構成" },
  { value: "l2", label: "L2 (VLAN)" },
  { value: "l3", label: "L3 (セグメント)" },
  { value: "logical", label: "論理構成" },
];

/** API のグラフを React Flow の形に変換する。
 *
 * `simple` は機器名と線だけの表示。現場で使う図に型番や保守状態が載っていると
 * 読み違いのもとになるので、用途で切り替えられるようにしてある。
 */
interface LabelOptions {
  portStyle: PortStyle;
  alongLine: boolean;
}

function toFlow(
  graph: TopologyGraph,
  simple: boolean,
  options: LabelOptions,
): {
  nodes: Node<DeviceNodeData>[];
  edges: Edge[];
} {
  const nodes = graph.nodes.map((node) => ({
    id: String(node.device_id),
    type: "device",
    position: { x: node.x, y: node.y },
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
    } satisfies DeviceNodeData,
  }));

  const edges = graph.edges.map((edge) => ({
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    // ポート名を両端に置くため、独自のエッジで描く
    type: "port",
    animated: false,
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
      warning: simple ? null : warningOf(edge),
    } satisfies PortEdgeData,
  }));

  return { nodes, edges };
}

function warningOf(edge: TopologyEdge): string | null {
  const warnings: string[] = [];
  if (edge.native_mismatch) warnings.push("ネイティブ不一致");
  if (edge.vlan_mismatch.length > 0) {
    warnings.push(`片側のみ VLAN ${edge.vlan_mismatch.slice(0, 4).join(",")}`);
  }
  return warnings.length ? warnings.join(" / ") : null;
}

function TopologyEditor() {
  const queryClient = useQueryClient();
  const [layer, setLayer] = useState("physical");
  // 選んだ VLAN を持つ機器だけを残す。空なら全部表示
  const [selectedVlans, setSelectedVlans] = useState<number[]>([]);
  const [selectedVrf, setSelectedVrf] = useState<string>("");
  // 拠点で絞る。現場では自分の拠点だけを見たいことが多い
  const [selectedSite, setSelectedSite] = useState<number | "">("");
  // VLAN が数百ある現場ではチェックボックスを全部出しても選べない
  const [vlanFilter, setVlanFilter] = useState("");
  // 機器名と線だけの表示。現場で使う図では付帯情報がむしろ邪魔になる
  const [simple, setSimple] = useState(false);
  // ポート名の出し方。長い名前が線に重なると図が読めなくなる
  const [portStyle, setPortStyle] = useState<PortStyle>("short");
  const [alongLine, setAlongLine] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<DeviceNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [dirty, setDirty] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const instance = useRef<ReactFlowInstance<Node<DeviceNodeData>, Edge> | null>(null);

  const { data: graph, isLoading } = useQuery({
    queryKey: ["topology", layer],
    queryFn: () => api.getTopology(layer),
  });
  const { data: stats } = useQuery({
    queryKey: ["topologyStats", layer],
    queryFn: () => api.getTopologyStats(layer),
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

  useEffect(() => {
    if (!graph) return;
    const flow = toFlow(graph, simple, { portStyle, alongLine });

    // VLAN / VRF を選んでいるときは、該当しない機器を薄くする。
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
      flow.nodes.forEach((n) => {
        if (!matches.has(n.id)) {
          n.style = { ...(n.style ?? {}), opacity: 0.22 };
        }
      });
      flow.edges.forEach((e) => {
        if (!matches.has(String(e.source)) || !matches.has(String(e.target))) {
          e.style = { ...(e.style ?? {}), opacity: 0.15 };
        }
      });
    }

    setNodes(flow.nodes);
    setEdges(flow.edges);
    setDirty(false);
  }, [
    graph,
    selectedVlans,
    selectedVrf,
    selectedSite,
    simple,
    portStyle,
    alongLine,
    setNodes,
    setEdges,
  ]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["topology"] });
    queryClient.invalidateQueries({ queryKey: ["topologyStats"] });
  };

  const save = useMutation({
    mutationFn: () =>
      api.savePositions(
        layer,
        nodes.map((n) => ({ device_id: Number(n.id), x: n.position.x, y: n.position.y })),
      ),
    onSuccess: () => {
      setDirty(false);
      setNotice("配置を保存しました");
      refresh();
    },
  });

  const reset = useMutation({
    mutationFn: () => api.resetPositions(layer),
    onSuccess: () => {
      setNotice("配置をリセットし、自動レイアウトに戻しました");
      refresh();
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
    onSuccess: () => {
      setNotice("結線を追加しました");
      refresh();
    },
  });

  const removeLink = useMutation({
    mutationFn: (id: number) => api.deleteLink(id),
    onSuccess: () => {
      setSelectedEdge(null);
      setNotice("結線を削除しました");
      refresh();
    },
  });

  const handleNodesChange = useCallback(
    (changes: NodeChange<Node<DeviceNodeData>>[]) => {
      onNodesChange(changes);
      if (changes.some((c) => c.type === "position" && c.dragging === false)) setDirty(true);
    },
    [onNodesChange],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source === connection.target) return;
      setEdges((current) =>
        addEdge({ ...connection, type: "smoothstep", className: "topo-edge manual" }, current),
      );
      createLink.mutate(connection);
    },
    [createLink, setEdges],
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

  return (
    <>
      <h1>トポロジ図</h1>
      <p className="subtitle">
        Config の LLDP / CDP から結線を推定して描きます。ノードをドラッグして配置を整え、
        ノード同士をつなげば手動で線を引けます。配置は保存され、機器が増えても崩れません。
      </p>

      <div className="toolbar">
        <select value={layer} onChange={(e) => setLayer(e.target.value)}>
          {LAYERS.map((item) => (
            <option key={item.value} value={item.value}>
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
        <button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {dirty ? "配置を保存" : "保存済み"}
        </button>
        <button onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
          Config から結線を作り直す
        </button>
        <button
          onClick={() => {
            if (confirm("配置をリセットして自動レイアウトに戻しますか？")) reset.mutate();
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
          })}
          title="設計書や報告書に貼れるベクタ形式で書き出します（Word / PowerPoint にそのまま挿入できます）"
        >
          図をファイルに出す（SVG{simple ? " / シンプル" : ""}）
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
      {(save.error || createLink.error || rebuild.error) && (
        <div className="error">
          {((save.error ?? createLink.error ?? rebuild.error) as Error).message}
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
              {stats.isolated_devices.length}
            </div>
          </div>
        </div>
      )}

      <div className="topo-canvas">
        {isLoading ? (
          <p className="muted" style={{ padding: 20 }}>
            読み込み中…
          </p>
        ) : nodes.length === 0 ? (
          <div className="empty" style={{ margin: 40 }}>
            表示できる機器がありません。台帳に機器を登録してください。
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeClick={(_, edge) => setSelectedEdge(edge)}
            onPaneClick={() => setSelectedEdge(null)}
            onInit={(flow) => {
              instance.current = flow;
              flow.fitView({ padding: 0.2 });
            }}
            fitView
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} color="#2a3340" />
            <Controls />
            <MiniMap pannable zoomable className="topo-minimap" />
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
              <span className="muted">
                同じ線で繋がって見えても、VRF が違えば通信できません
              </span>
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

      {selectedEdge && (
        <div className="card" style={{ marginTop: 12 }}>
          選択中の結線: <strong>{String(selectedEdge.label ?? "")}</strong>
          <div className="actions">
            <button className="danger" onClick={() => removeLink.mutate(Number(selectedEdge.id))}>
              この結線を削除
            </button>
            <button onClick={() => setSelectedEdge(null)}>選択を解除</button>
          </div>
        </div>
      )}

      {stats && stats.isolated_devices.length > 0 && (
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
