import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { api } from "../api/client";
import type { TopologyGraph } from "../api/types";

/* ネットワークを立体で眺めるページ。
 *
 * 平面の構成図は「どこがどこに繋がっているか」を読むためのもので、それは
 * トポロジ図が担当する。こちらは全体の形をつかむための遊びで、上位ほど上、
 * 配下ほど下に並べた層を回して見る。作業には使わない。
 *
 * 外部ライブラリは入れない（閉域で動かす前提なので、依存は増やさない）。
 * Canvas に自前で投影するだけで足りる。
 */

interface Point {
  id: number;
  hostname: string;
  siteName: string | null;
  deviceType: string;
  x: number;
  y: number;
  z: number;
  // 揺れの位相。全部が同じ動きをすると機械的に見える
  phase: number;
  // 光の玉が着いた時刻。しばらく明るくする
  litAt: number;
  /* 画面に投影した後の位置。線を引くときに使い回す */
  screenX: number;
  screenY: number;
  scale: number;
}

const TYPE_COLOR: Record<string, string> = {
  router: "#7cc4ff",
  firewall: "#ff9d7c",
  switch: "#7ce0a8",
  load_balancer: "#d5a6ff",
  wireless_controller: "#ffd77c",
  wireless_ap: "#ffe9b0",
  server: "#c0c8d4",
  other: "#8a94a6",
};

const LAYER_GAP = 150;
const RADIUS = 240;

/* 網の中心から数えて何段目かを返す。
 *
 * 端から端（一番長い経路）の真ん中が中心。上位がそこに来るので、
 * 平面図と同じ順で層になる。繋がっている本数で決めると、配下の多い
 * 支社が中心になってしまう。
 */
function layersOf(graph: TopologyGraph): Map<number, number> {
  const near = new Map<number, number[]>();
  for (const node of graph.nodes) near.set(node.device_id, []);
  for (const edge of graph.edges) {
    near.get(edge.source)?.push(edge.target);
    near.get(edge.target)?.push(edge.source);
  }

  const walk = (from: number) => {
    const distance = new Map<number, number>([[from, 0]]);
    const queue = [from];
    let last = from;
    while (queue.length) {
      const current = queue.shift()!;
      last = current;
      for (const next of near.get(current) ?? []) {
        if (distance.has(next)) continue;
        distance.set(next, distance.get(current)! + 1);
        queue.push(next);
      }
    }
    return { last, distance };
  };

  const layers = new Map<number, number>();
  const remaining = new Set(near.keys());
  while (remaining.size) {
    const seed = remaining.values().next().value as number;
    const a = walk(seed);
    const b = walk(a.last);
    const c = walk(b.last);
    let centre = seed;
    let best = Infinity;
    for (const id of b.distance.keys()) {
      const far = Math.max(b.distance.get(id) ?? 0, c.distance.get(id) ?? 0);
      if (far < best) {
        best = far;
        centre = id;
      }
    }
    for (const [id, hop] of walk(centre).distance) {
      layers.set(id, hop);
      remaining.delete(id);
    }
  }
  return layers;
}

/* 段ごとに輪を作って配置する。上位が上、配下が下。 */
function toPoints(graph: TopologyGraph): Point[] {
  const layers = layersOf(graph);
  const depths = [...new Set(layers.values())].sort((a, b) => a - b);
  const counts = new Map<number, number>();
  for (const hop of layers.values()) counts.set(hop, (counts.get(hop) ?? 0) + 1);
  const index = new Map<number, number>();

  return graph.nodes.map((node) => {
    const hop = layers.get(node.device_id) ?? 0;
    const position = index.get(hop) ?? 0;
    index.set(hop, position + 1);
    const count = counts.get(hop) ?? 1;
    const angle = (position / count) * Math.PI * 2;
    // 台数が多い段ほど輪を大きくして、点が重ならないようにする
    const radius = count === 1 ? 0 : Math.max(RADIUS * 0.4, (count * 26) / (Math.PI * 2));

    return {
      id: node.device_id,
      hostname: node.hostname,
      siteName: node.site_name,
      deviceType: node.device_type,
      phase: Math.random() * Math.PI * 2,
      litAt: -Infinity,
      x: Math.cos(angle) * radius,
      y: (depths.indexOf(hop) - (depths.length - 1) / 2) * LAYER_GAP,
      z: Math.sin(angle) * radius,
      screenX: 0,
      screenY: 0,
      scale: 1,
    };
  });
}

/* 線の上を流れる光の玉。通信している様子を眺めるためだけのもので、
 * 実際のトラフィックとは関係が無い。行きと帰りの両方をランダムに流す。 */
interface Packet {
  edge: number;
  from: number;
  to: number;
  progress: number;
  speed: number;
}

const MAX_PACKETS = 90;

export default function SpacePage() {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [site, setSite] = useState<number | "">("");
  const [spin, setSpin] = useState(true);
  // 見る向き。ドラッグで回す
  const angle = useRef({ yaw: 0.6, pitch: -0.35 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const hovered = useRef<Point | null>(null);

  const { data: sites } = useQuery({
    queryKey: ["sites"],
    queryFn: () => api.listSites(),
  });
  const { data: graph } = useQuery({
    queryKey: ["topology", "space", site],
    queryFn: () =>
      api.getTopology("physical", false, "vertical", {
        site_id: site === "" ? undefined : site,
      }),
  });

  const points = useMemo(() => (graph ? toPoints(graph) : []), [graph]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d");
    if (!context) return;

    let running = true;
    const byId = new Map(points.map((p) => [p.id, p]));
    const edges = (graph?.edges ?? []).filter(
      (edge) => byId.has(edge.source) && byId.has(edge.target),
    );
    const packets: Packet[] = [];
    const started = performance.now();

    const draw = () => {
      if (!running) return;
      const width = (element.width = element.clientWidth * devicePixelRatio);
      const height = (element.height = element.clientHeight * devicePixelRatio);
      context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.fillStyle = "#0d1117";
      context.fillRect(0, 0, width, height);

      const now = performance.now();
      const seconds = (now - started) / 1000;
      if (spin && !drag.current) angle.current.yaw += 0.003;

      const { yaw, pitch } = angle.current;
      const cx = element.clientWidth / 2;
      const cy = element.clientHeight / 2;
      const distance = 900;

      for (const point of points) {
        // 止まっていると絵に見えるので、少しだけ揺らす
        const driftX = Math.sin(seconds * 0.5 + point.phase) * 6;
        const driftY = Math.sin(seconds * 0.35 + point.phase * 1.7) * 5;
        const driftZ = Math.cos(seconds * 0.45 + point.phase) * 6;

        // y 軸まわりに回し、次に x 軸まわりに傾ける
        const px = point.x + driftX;
        const py = point.y + driftY;
        const pz = point.z + driftZ;
        const x1 = px * Math.cos(yaw) - pz * Math.sin(yaw);
        const z1 = px * Math.sin(yaw) + pz * Math.cos(yaw);
        const y2 = py * Math.cos(pitch) - z1 * Math.sin(pitch);
        const z2 = py * Math.sin(pitch) + z1 * Math.cos(pitch);

        const scale = distance / (distance + z2 + 400);
        point.screenX = cx + x1 * scale;
        point.screenY = cy + y2 * scale;
        point.scale = scale;
      }

      // 線が先。奥のものから描くと重なりが自然に見える
      context.lineWidth = 1;
      for (const edge of graph?.edges ?? []) {
        const a = byId.get(edge.source);
        const b = byId.get(edge.target);
        if (!a || !b) continue;
        const depth = (a.scale + b.scale) / 2;
        context.strokeStyle = `rgba(124, 196, 255, ${0.15 + depth * 0.35})`;
        context.beginPath();
        context.moveTo(a.screenX, a.screenY);
        context.lineTo(b.screenX, b.screenY);
        context.stroke();
      }

      // 光の玉を流す。行き先はランダムで、行きと帰りの両方がある
      if (edges.length && packets.length < MAX_PACKETS && Math.random() < 0.35) {
        const index = Math.floor(Math.random() * edges.length);
        const edge = edges[index];
        const forward = Math.random() < 0.5;
        packets.push({
          edge: index,
          from: forward ? edge.source : edge.target,
          to: forward ? edge.target : edge.source,
          progress: 0,
          speed: 0.006 + Math.random() * 0.012,
        });
      }

      for (let i = packets.length - 1; i >= 0; i -= 1) {
        const packet = packets[i];
        const from = byId.get(packet.from);
        const to = byId.get(packet.to);
        if (!from || !to) {
          packets.splice(i, 1);
          continue;
        }
        packet.progress += packet.speed;
        if (packet.progress >= 1) {
          // 着いた先を少し光らせる。届いたことが分かる
          to.litAt = now;
          packets.splice(i, 1);
          continue;
        }

        const x = from.screenX + (to.screenX - from.screenX) * packet.progress;
        const y = from.screenY + (to.screenY - from.screenY) * packet.progress;
        const scale = from.scale + (to.scale - from.scale) * packet.progress;

        // 尾を引かせる。点だけだと流れているように見えない
        const tail = Math.max(0, packet.progress - 0.12);
        const tailX = from.screenX + (to.screenX - from.screenX) * tail;
        const tailY = from.screenY + (to.screenY - from.screenY) * tail;
        const gradient = context.createLinearGradient(tailX, tailY, x, y);
        gradient.addColorStop(0, "rgba(160, 220, 255, 0)");
        gradient.addColorStop(1, `rgba(200, 240, 255, ${0.5 + scale * 0.5})`);
        context.strokeStyle = gradient;
        context.lineWidth = 1.6 * scale;
        context.beginPath();
        context.moveTo(tailX, tailY);
        context.lineTo(x, y);
        context.stroke();

        context.fillStyle = "#dff2ff";
        context.shadowColor = "rgba(140, 210, 255, 0.9)";
        context.shadowBlur = 12 * scale;
        context.beginPath();
        context.arc(x, y, 2.2 * scale, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
      }

      const sorted = [...points].sort((a, b) => a.scale - b.scale);
      for (const point of sorted) {
        const size = 4 + point.scale * 5;
        // 玉が届いた直後は明るくする（0.6 秒ほどで戻す）
        const lit = Math.max(0, 1 - (now - point.litAt) / 600);
        context.fillStyle = TYPE_COLOR[point.deviceType] ?? TYPE_COLOR.other;
        context.globalAlpha = 0.35 + point.scale * 0.65;
        if (lit > 0) {
          context.shadowColor = "rgba(190, 235, 255, 0.9)";
          context.shadowBlur = 18 * lit * point.scale;
        }
        context.beginPath();
        context.arc(point.screenX, point.screenY, size + lit * 2, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
        context.globalAlpha = 1;

        // 名前は手前のものだけ。全部出すと文字で埋まる
        if (point.scale > 0.95 || point === hovered.current) {
          context.fillStyle = "#c9d4e2";
          context.font = "11px system-ui, sans-serif";
          context.fillText(point.hostname, point.screenX + size + 3, point.screenY + 4);
        }
      }

      requestAnimationFrame(draw);
    };

    requestAnimationFrame(draw);
    return () => {
      running = false;
    };
  }, [points, graph, spin]);

  return (
    <section>
      <h1>立体で眺める</h1>
      <p className="muted">
        全体の形をつかむための遊びのページです。どこがどこに繋がっているかを読むのは
        <RouterLink to="/topology"> トポロジ図 </RouterLink>
        の方が向いています。ドラッグで回せます。
      </p>

      <div className="toolbar">
        <select
          value={site}
          onChange={(event) => setSite(event.target.value === "" ? "" : Number(event.target.value))}
        >
          <option value="">拠点: すべて</option>
          {(sites ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              拠点: {item.name}
            </option>
          ))}
        </select>
        <label className="vlan-chip">
          <input type="checkbox" checked={spin} onChange={(e) => setSpin(e.target.checked)} />{" "}
          回し続ける
        </label>
        <span className="muted">
          {graph ? `${graph.nodes.length}台 ${graph.edges.length}本` : "読み込み中…"}
        </span>
      </div>

      <canvas
        ref={canvas}
        className="space-canvas"
        onPointerDown={(event) => {
          drag.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          angle.current.yaw += (event.clientX - drag.current.x) * 0.008;
          angle.current.pitch += (event.clientY - drag.current.y) * 0.008;
          angle.current.pitch = Math.max(-1.4, Math.min(1.4, angle.current.pitch));
          drag.current = { x: event.clientX, y: event.clientY };
        }}
      />
    </section>
  );
}
