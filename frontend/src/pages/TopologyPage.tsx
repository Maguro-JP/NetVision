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

const NODE_TYPES = { device: DeviceNode };

const LAYERS = [
  { value: "physical", label: "物理構成" },
  { value: "l2", label: "L2 (VLAN)" },
  { value: "l3", label: "L3 (セグメント)" },
  { value: "logical", label: "論理構成" },
];

/** API のグラフを React Flow の形に変換する。 */
function toFlow(graph: TopologyGraph): {
  nodes: Node<DeviceNodeData>[];
  edges: Edge[];
} {
  const nodes = graph.nodes.map((node) => ({
    id: String(node.device_id),
    type: "device",
    position: { x: node.x, y: node.y },
    data: {
      hostname: node.hostname,
      vendor: node.vendor,
      model: node.model,
      deviceType: node.device_type,
      severity: node.severity,
      siteName: node.site_name,
      mgmtIp: node.mgmt_ip,
      interfaceCount: node.interface_count,
    } satisfies DeviceNodeData,
  }));

  const edges = graph.edges.map((edge) => ({
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    label: edgeLabel(edge),
    // 直交ルーティングにすると、機器が格子状に並ぶ構成図で線が読みやすい
    type: "smoothstep",
    animated: false,
    className: `topo-edge ${edge.origin} ${edge.confirmed_both_ways ? "confirmed" : ""}`,
    data: { origin: edge.origin },
  }));

  return { nodes, edges };
}

function edgeLabel(edge: TopologyEdge): string {
  if (edge.label) return edge.label;
  const ends = [edge.source_interface, edge.target_interface].filter(Boolean);
  return ends.length === 2 ? `${shorten(ends[0])} — ${shorten(ends[1])}` : ends[0] ?? "";
}

/** 図が読めなくなるので、ポート名は略記で出す。 */
function shorten(name: string): string {
  return name
    .replace(/^GigabitEthernet/i, "Gi")
    .replace(/^TenGigabitEthernet/i, "Te")
    .replace(/^FastEthernet/i, "Fa")
    .replace(/^Port-channel/i, "Po");
}

function TopologyEditor() {
  const queryClient = useQueryClient();
  const [layer, setLayer] = useState("physical");
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

  useEffect(() => {
    if (!graph) return;
    const flow = toFlow(graph);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setDirty(false);
  }, [graph, setNodes, setEdges]);

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
        <a
          className="button"
          href={api.topologyExportUrl(layer)}
          title="設計書や報告書に貼れるベクタ形式で書き出します（Word / PowerPoint にそのまま挿入できます）"
        >
          図をファイルに出す（SVG）
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
        {legend.map((item) => (
          <span key={item.text} className="topo-legend">
            <span className={`topo-legend-line ${item.className}`} /> {item.text}
          </span>
        ))}
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
