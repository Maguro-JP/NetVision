import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { api } from "../api/client";
import type { Site, TopologyGraph } from "../api/types";
import { BOUNDS, locate } from "../lib/japan";

/* ネットワークを立体で眺めるページ。
 *
 * 平面の構成図は「どこがどこに繋がっているか」を読むためのもので、それは
 * トポロジ図が担当する。こちらは全体の形をつかむための遊び。
 *
 * 流れている光はランダムに出しているだけで、実際の通信量ではない。
 * 監視の画面と取り違えられないよう、画面にもそう書いてある。
 *
 * 外部ライブラリも外部の地図サービスも使わない（閉域で動かす前提）。
 * Canvas に自前で投影し、住所から座標を出す表も同梱している。
 */

type Mode = "layer" | "sphere" | "map";

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
  // 投影した後の位置。線と玉で使い回す
  screenX: number;
  screenY: number;
  scale: number;
}

interface Packet {
  from: number;
  to: number;
  progress: number;
  speed: number;
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

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "sphere", label: "球", hint: "上下左右前後に広がる形で見ます" },
  { value: "layer", label: "層", hint: "上位を上に、段ごとの輪で見ます" },
  { value: "map", label: "地図", hint: "拠点の住所から日本地図に置いて見ます" },
];

const LAYER_GAP = 150;
const RADIUS = 240;
const MAX_PACKETS = 90;

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

function base(graph: TopologyGraph): Point[] {
  return graph.nodes.map((node) => ({
    id: node.device_id,
    hostname: node.hostname,
    siteName: node.site_name,
    deviceType: node.device_type,
    x: 0,
    y: 0,
    z: 0,
    phase: Math.random() * Math.PI * 2,
    litAt: -Infinity,
    screenX: 0,
    screenY: 0,
    scale: 1,
  }));
}

/* 段ごとの輪。上位が上、配下が下。 */
function asLayers(points: Point[], layers: Map<number, number>): void {
  const depths = [...new Set(layers.values())].sort((a, b) => a - b);
  const counts = new Map<number, number>();
  for (const hop of layers.values()) counts.set(hop, (counts.get(hop) ?? 0) + 1);
  const index = new Map<number, number>();

  for (const point of points) {
    const hop = layers.get(point.id) ?? 0;
    const position = index.get(hop) ?? 0;
    index.set(hop, position + 1);
    const count = counts.get(hop) ?? 1;
    const angle = (position / count) * Math.PI * 2;
    const radius = count === 1 ? 0 : Math.max(RADIUS * 0.4, (count * 26) / (Math.PI * 2));

    point.x = Math.cos(angle) * radius;
    point.y = (depths.indexOf(hop) - (depths.length - 1) / 2) * LAYER_GAP;
    point.z = Math.sin(angle) * radius;
  }
}

/* 球。上位を内側、配下を外側の殻に置き、全方向へ線が伸びる形にする。
 *
 * 殻の中では黄金角で散らす。等間隔に並べると縞が見えて、
 * 網ではなく模様に見えてしまう。
 */
function asSphere(points: Point[], layers: Map<number, number>): void {
  const depths = [...new Set(layers.values())].sort((a, b) => a - b);
  const counts = new Map<number, number>();
  for (const hop of layers.values()) counts.set(hop, (counts.get(hop) ?? 0) + 1);
  const index = new Map<number, number>();
  const golden = Math.PI * (3 - Math.sqrt(5));

  for (const point of points) {
    const hop = layers.get(point.id) ?? 0;
    const position = index.get(hop) ?? 0;
    index.set(hop, position + 1);
    const count = counts.get(hop) ?? 1;
    const shell = depths.indexOf(hop);
    const radius = 90 + shell * (RADIUS / Math.max(depths.length - 1, 1));

    // 殻の上に散らす（1台なら中心付近）
    const t = count === 1 ? 0.5 : (position + 0.5) / count;
    const y = 1 - t * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * (position + shell * 7);

    point.x = Math.cos(theta) * ring * radius;
    point.y = y * radius;
    point.z = Math.sin(theta) * ring * radius;
  }
}

/* 地図。拠点の住所から日本地図に置く。高さは段（上位ほど高い）。
 *
 * 住所から位置が分からない機器は地図に置かない。それらしい場所に置くと、
 * 地図の上では「そこにある」と読まれてしまう。下の余白にまとめる。
 */
function asMap(
  points: Point[],
  layers: Map<number, number>,
  addressOf: Map<string, string | null>,
): number {
  const depths = [...new Set(layers.values())].sort((a, b) => a - b);
  const spread = new Map<string, number>();
  let unknown = 0;

  for (const point of points) {
    const site = point.siteName ?? "";
    const at = locate(site, addressOf.get(site) ?? null);
    const hop = layers.get(point.id) ?? 0;
    const height = (depths.length - 1 - depths.indexOf(hop)) * 60;

    if (!at) {
      // 置き場所が無いものは地図の外へ並べる
      point.x = -420 + (unknown % 12) * 70;
      point.y = 260 + Math.floor(unknown / 12) * 40;
      point.z = 320;
      unknown += 1;
      continue;
    }

    // 同じ県に何台もあるので、少しずらして重ならないようにする
    const seen = spread.get(site) ?? 0;
    spread.set(site, seen + 1);
    const jitter = seen === 0 ? 0 : 12 + seen * 4;
    const angle = seen * 2.4;

    const width = BOUNDS.maxLon - BOUNDS.minLon;
    const depth = BOUNDS.maxLat - BOUNDS.minLat;
    point.x = ((at.lon - (BOUNDS.minLon + width / 2)) / width) * 900 + Math.cos(angle) * jitter;
    point.z = ((BOUNDS.minLat + depth / 2 - at.lat) / depth) * 700 + Math.sin(angle) * jitter;
    point.y = -height;
  }
  return unknown;
}

export default function SpacePage() {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [site, setSite] = useState<number | "">("");
  const [mode, setMode] = useState<Mode>("sphere");
  const [spin, setSpin] = useState(true);
  const [flow, setFlow] = useState(true);
  // 見る向き。ドラッグで回す
  const angle = useRef({ yaw: 0.6, pitch: -0.35 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: () => api.listSites() });
  const { data: graph } = useQuery({
    queryKey: ["topology", "space", site],
    queryFn: () =>
      api.getTopology("physical", false, "vertical", {
        site_id: site === "" ? undefined : site,
      }),
  });

  const addressOf = useMemo(
    () => new Map((sites ?? []).map((item: Site) => [item.name, item.address])),
    [sites],
  );

  // 表示を切り替えたら見る向きも合わせる。地図は真上から見ないと形が読めない
  useEffect(() => {
    angle.current = mode === "map" ? { yaw: 0, pitch: -1.15 } : { yaw: 0.6, pitch: -0.35 };
    // 地図は回すと形が読めなくなるので、既定では止めておく
    setSpin(mode !== "map");
  }, [mode]);

  const { points, unlocated } = useMemo(() => {
    if (!graph) return { points: [] as Point[], unlocated: 0 };
    const made = base(graph);
    const layers = layersOf(graph);
    if (mode === "layer") asLayers(made, layers);
    else if (mode === "sphere") asSphere(made, layers);
    const missing = mode === "map" ? asMap(made, layers, addressOf) : 0;
    return { points: made, unlocated: missing };
  }, [graph, mode, addressOf]);

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

    // 地図では線を弧にする。真っ直ぐ引くと重なって本数が分からない
    const arc = mode === "map" ? 0.32 : 0;

    const draw = () => {
      if (!running) return;
      element.width = element.clientWidth * devicePixelRatio;
      element.height = element.clientHeight * devicePixelRatio;
      context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      context.fillStyle = "#0d1117";
      context.fillRect(0, 0, element.clientWidth, element.clientHeight);

      const now = performance.now();
      const seconds = (now - started) / 1000;
      if (spin && !drag.current) angle.current.yaw += 0.003;

      const { yaw, pitch } = angle.current;
      const cx = element.clientWidth / 2;
      const cy = element.clientHeight / 2;
      const distance = 900;

      const project = (x: number, y: number, z: number) => {
        const x1 = x * Math.cos(yaw) - z * Math.sin(yaw);
        const z1 = x * Math.sin(yaw) + z * Math.cos(yaw);
        const y2 = y * Math.cos(pitch) - z1 * Math.sin(pitch);
        const z2 = y * Math.sin(pitch) + z1 * Math.cos(pitch);
        const scale = distance / (distance + z2 + 400);
        return { sx: cx + x1 * scale, sy: cy + y2 * scale, scale };
      };

      for (const point of points) {
        // 止まっていると絵に見えるので、少しだけ揺らす
        const driftX = Math.sin(seconds * 0.5 + point.phase) * 6;
        const driftY = Math.sin(seconds * 0.35 + point.phase * 1.7) * 5;
        const driftZ = Math.cos(seconds * 0.45 + point.phase) * 6;
        const spot = project(point.x + driftX, point.y + driftY, point.z + driftZ);
        point.screenX = spot.sx;
        point.screenY = spot.sy;
        point.scale = spot.scale;
      }

      // 線の途中（弧の頂点）。地図では持ち上げて航路のように見せる
      const control = (a: Point, b: Point) => {
        if (!arc) return { x: (a.screenX + b.screenX) / 2, y: (a.screenY + b.screenY) / 2 };
        const midX = (a.x + b.x) / 2;
        const midZ = (a.z + b.z) / 2;
        const span = Math.hypot(a.x - b.x, a.z - b.z);
        const spot = project(midX, (a.y + b.y) / 2 - span * arc, midZ);
        return { x: spot.sx, y: spot.sy };
      };

      const at = (a: Point, b: Point, c: { x: number; y: number }, t: number) => {
        const u = 1 - t;
        return {
          x: u * u * a.screenX + 2 * u * t * c.x + t * t * b.screenX,
          y: u * u * a.screenY + 2 * u * t * c.y + t * t * b.screenY,
        };
      };

      // 地図では緯度経度の目盛りを薄く敷く。点だけだと地図に見えない
      if (mode === "map") {
        context.strokeStyle = "rgba(124, 196, 255, 0.08)";
        context.lineWidth = 1;
        for (let lon = BOUNDS.minLon; lon <= BOUNDS.maxLon; lon += 5) {
          const width = BOUNDS.maxLon - BOUNDS.minLon;
          const x = ((lon - (BOUNDS.minLon + width / 2)) / width) * 900;
          const a = project(x, 0, -350);
          const b = project(x, 0, 350);
          context.beginPath();
          context.moveTo(a.sx, a.sy);
          context.lineTo(b.sx, b.sy);
          context.stroke();
        }
        for (let lat = BOUNDS.minLat; lat <= BOUNDS.maxLat; lat += 5) {
          const depth = BOUNDS.maxLat - BOUNDS.minLat;
          const z = ((BOUNDS.minLat + depth / 2 - lat) / depth) * 700;
          const a = project(-450, 0, z);
          const b = project(450, 0, z);
          context.beginPath();
          context.moveTo(a.sx, a.sy);
          context.lineTo(b.sx, b.sy);
          context.stroke();
        }
      }

      context.lineWidth = 1;
      const curves = new Map<number, { x: number; y: number }>();
      for (const edge of edges) {
        const a = byId.get(edge.source)!;
        const b = byId.get(edge.target)!;
        const mid = control(a, b);
        curves.set(edge.id, mid);
        const depth = (a.scale + b.scale) / 2;
        context.strokeStyle = `rgba(124, 196, 255, ${0.12 + depth * 0.3})`;
        context.beginPath();
        context.moveTo(a.screenX, a.screenY);
        context.quadraticCurveTo(mid.x, mid.y, b.screenX, b.screenY);
        context.stroke();
      }

      // 光の玉。行き先はランダムで、行きと帰りの両方がある
      if (flow && edges.length && packets.length < MAX_PACKETS && Math.random() < 0.35) {
        const edge = edges[Math.floor(Math.random() * edges.length)];
        const forward = Math.random() < 0.5;
        packets.push({
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

        const mid = control(from, to);
        const head = at(from, to, mid, packet.progress);
        const tail = at(from, to, mid, Math.max(0, packet.progress - 0.12));
        const scale = from.scale + (to.scale - from.scale) * packet.progress;

        const gradient = context.createLinearGradient(tail.x, tail.y, head.x, head.y);
        gradient.addColorStop(0, "rgba(160, 220, 255, 0)");
        gradient.addColorStop(1, `rgba(200, 240, 255, ${0.5 + scale * 0.5})`);
        context.strokeStyle = gradient;
        context.lineWidth = 1.6 * scale;
        context.beginPath();
        context.moveTo(tail.x, tail.y);
        context.lineTo(head.x, head.y);
        context.stroke();

        context.fillStyle = "#dff2ff";
        context.shadowColor = "rgba(140, 210, 255, 0.9)";
        context.shadowBlur = 12 * scale;
        context.beginPath();
        context.arc(head.x, head.y, 2.2 * scale, 0, Math.PI * 2);
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
        if (point.scale > 0.95) {
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
  }, [points, graph, spin, flow, mode]);

  return (
    <section>
      <h1>立体で眺める</h1>
      <p className="muted">
        全体の形をつかむための遊びのページです。流れている光はランダムに出しているだけで、
        実際の通信量ではありません。どこがどこに繋がっているかを読むのは
        <RouterLink to="/topology"> トポロジ図 </RouterLink>
        の方が向いています。ドラッグで回せます。
      </p>

      <div className="toolbar">
        <select value={mode} onChange={(event) => setMode(event.target.value as Mode)}>
          {MODES.map((item) => (
            <option key={item.value} value={item.value}>
              表示: {item.label}
            </option>
          ))}
        </select>
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
        <label className="vlan-chip">
          <input type="checkbox" checked={flow} onChange={(e) => setFlow(e.target.checked)} /> 光を
          流す
        </label>
        <span className="muted">
          {graph ? `${graph.nodes.length}台 ${graph.edges.length}本` : "読み込み中…"}
          {mode === "map" && unlocated > 0 ? ` / 住所から位置が分からない機器 ${unlocated}台` : ""}
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
