import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { api } from "../api/client";
import type { Site, TopologyGraph } from "../api/types";
import { COASTLINE } from "../lib/coastline";
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

type Mode = "layer" | "sphere" | "tree" | "map";

interface Point {
  id: number;
  hostname: string;
  siteName: string | null;
  deviceType: string;
  severity: string;
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

/* 状態の色。ふだんの機器は控えめにして、要注意のものだけ強く出す。
 * 全部が同じ明るさだと、見てほしいものが埋もれる。 */
const ALERT_COLOR: Record<string, string> = {
  expired: "#ff5f56",
  critical: "#ff7b3d",
  warning: "#ffc24b",
};

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "sphere", label: "球", hint: "上下左右前後に広がる形で見ます" },
  { value: "layer", label: "層", hint: "上位を上に、段ごとの輪で見ます" },
  { value: "tree", label: "木", hint: "上位から枝分かれする形で見ます" },
  { value: "map", label: "地図", hint: "拠点の住所から日本地図に置いて見ます" },
];

const LAYER_GAP = 150;
const RADIUS = 240;

/* 見え方の設定。好みが分かれるところなので、全部その場で変えられるようにする。
 * 端末に覚えさせるので、開き直しても選び直さずに済む。 */
interface Settings {
  spin: number; // 回転の速さ。負なら逆回り
  drift: number; // 放置中の揺れの大きさ
  flow: number; // 光の玉の出やすさ（0 で出さない）
  flowSpeed: number; // 光の玉の速さ
  dot: number; // 機器の丸の大きさ
  link: number; // 線の明るさ
  labels: boolean; // 手前の機器名を出すか
  alerts: boolean; // 要注意の機器を強調するか
  turning: boolean; // 回すか止めるか（速さは覚えたまま）
}

const DEFAULTS: Settings = {
  spin: 0.0012,
  drift: 6,
  flow: 0.35,
  flowSpeed: 1,
  dot: 1,
  link: 1,
  labels: true,
  alerts: true,
  turning: true,
};

/* この拡大率より引くと、拠点ごとに1つの玉へまとめる。
 * ネットワークは小さなネットワークの集まりなので、引いたときは
 * 拠点そのものを1点として見た方が形が分かる。寄れば中身が出る。 */
const CLUSTER_ZOOM = 1.5;

const SETTINGS_KEY = "netvision.space.settings";

function loadSettings(): Settings {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") };
  } catch {
    return DEFAULTS;
  }
}

const MAX_PACKETS = 160;

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
    severity: node.severity,
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

/* 緯度経度を地図の平面（x が東西、z が南北）に置く。 */
function onMap(lat: number, lon: number): { x: number; z: number } {
  const width = BOUNDS.maxLon - BOUNDS.minLon;
  const depth = BOUNDS.maxLat - BOUNDS.minLat;
  return {
    x: ((lon - (BOUNDS.minLon + width / 2)) / width) * 900,
    z: ((BOUNDS.minLat + depth / 2 - lat) / depth) * 700,
  };
}

/* 木。親から枝分かれさせて、枝ごとに向きを変える。
 *
 * 層と違って、同じ段でも系統ごとにまとまるので、どの支社の配下かが見える。
 */
function asTree(points: Point[], graph: TopologyGraph, layers: Map<number, number>): void {
  const byId = new Map(points.map((p) => [p.id, p]));
  const near = new Map<number, number[]>();
  for (const point of points) near.set(point.id, []);
  for (const edge of graph.edges) {
    near.get(edge.source)?.push(edge.target);
    near.get(edge.target)?.push(edge.source);
  }

  const order = [...byId.keys()].sort(
    (a, b) => (layers.get(a) ?? 0) - (layers.get(b) ?? 0) || a - b,
  );
  const seen = new Set<number>();

  // 根から順に、子を扇状に広げていく
  const place = (id: number, depth: number, centre: number, spread: number) => {
    const point = byId.get(id)!;
    const radius = depth === 0 ? 0 : 90 + depth * 90;
    point.x = Math.cos(centre) * radius;
    point.y = depth * 110 - 160;
    point.z = Math.sin(centre) * radius;

    const kids = (near.get(id) ?? []).filter((child) => !seen.has(child));
    kids.forEach((child) => seen.add(child));
    kids.forEach((child, index) => {
      const step = spread / Math.max(kids.length, 1);
      const angle = centre - spread / 2 + step * (index + 0.5);
      place(child, depth + 1, angle, Math.min(spread, step * 2.2));
    });
  };

  const roots = order.filter((id) => (layers.get(id) ?? 0) === (layers.get(order[0]) ?? 0));
  roots.forEach((id, index) => {
    if (seen.has(id)) return;
    seen.add(id);
    place(id, 0, (index / Math.max(roots.length, 1)) * Math.PI * 2, Math.PI * 2);
  });
  for (const id of order) {
    if (!seen.has(id)) {
      seen.add(id);
      place(id, 0, Math.random() * Math.PI * 2, Math.PI * 2);
    }
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

    const spot = onMap(at.lat, at.lon);
    point.x = spot.x + Math.cos(angle) * jitter;
    point.z = spot.z + Math.sin(angle) * jitter;
    point.y = -height;
  }
  return unknown;
}

/* 調整用のつまみ。数値も出しておかないと、どこまで動かしたか分からなくなる。 */
function Slider({
  label,
  hint,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-slider" title={hint}>
      <span>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <span className="muted">{value}</span>
    </label>
  );
}

/* 拠点ごとに1つへまとめた見え方を作る。
 *
 * 位置は所属する機器の平均。線は拠点間に1本だけ引く。
 * 拠点をまたがない結線（拠点の中の配線）は、まとめた側には出さない。 */
function clusterOf(
  points: Point[],
  edges: { source: number; target: number }[],
): {
  points: Point[];
  edges: { id: number; source: number; target: number }[];
  counts: Map<number, number>;
} {
  const groups = new Map<string, Point[]>();
  for (const point of points) {
    const key = point.siteName ?? "拠点未登録";
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(point);
  }

  const clusterId = new Map<number, number>();
  const made: Point[] = [];
  const counts = new Map<number, number>();
  let next = -1;
  const rank: Record<string, number> = { expired: 3, critical: 2, warning: 1 };

  for (const [name, members] of groups) {
    const id = next--;
    for (const member of members) clusterId.set(member.id, id);
    counts.set(id, members.length);
    // 状態は一番悪いものに合わせる。まとめたせいで見落とすのは避ける
    const worst = members.reduce(
      (severity, member) =>
        (rank[member.severity] ?? 0) > (rank[severity] ?? 0) ? member.severity : severity,
      "ok",
    );
    made.push({
      id,
      hostname: name,
      siteName: name,
      deviceType: "other",
      severity: worst,
      x: members.reduce((total, m) => total + m.x, 0) / members.length,
      y: members.reduce((total, m) => total + m.y, 0) / members.length,
      z: members.reduce((total, m) => total + m.z, 0) / members.length,
      phase: members[0].phase,
      litAt: -Infinity,
      screenX: 0,
      screenY: 0,
      scale: 1,
    });
  }

  const seen = new Set<string>();
  const links: { id: number; source: number; target: number }[] = [];
  for (const edge of edges) {
    const a = clusterId.get(edge.source);
    const b = clusterId.get(edge.target);
    if (a === undefined || b === undefined || a === b) continue;
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (seen.has(key)) continue;
    seen.add(key);
    links.push({ id: links.length + 1, source: a, target: b });
  }
  return { points: made, edges: links, counts };
}

export default function SpacePage() {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [site, setSite] = useState<number | "">("");
  const [mode, setMode] = useState<Mode>("sphere");
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setSettings((current) => ({ ...current, [key]: value }));
  // 見る向き。ドラッグで回す
  const angle = useRef({ yaw: 0.6, pitch: -0.35 });
  // 実際に効いている回転。人が回した向きをそのまま引き継ぐ
  const spin = useRef(settings.spin);
  // 中心の位置と拡大率。Shift か右ボタンのドラッグで動かし、ホイールで寄る
  const pan = useRef({ x: 0, y: 0 });
  const zoom = useRef(1);
  const drag = useRef<{ x: number; y: number; pan: boolean; spin: number } | null>(null);

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
    pan.current = { x: 0, y: 0 };
    zoom.current = 1;
    // 地図は回すと形が読めなくなるので、切り替えた直後は止めておく
    spin.current = mode === "map" ? 0 : settings.spin;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // 設定は端末に覚えさせる
  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    spin.current = settings.spin;
  }, [settings]);

  const { points, unlocated } = useMemo(() => {
    if (!graph) return { points: [] as Point[], unlocated: 0 };
    const made = base(graph);
    const layers = layersOf(graph);
    if (mode === "layer") asLayers(made, layers);
    else if (mode === "sphere") asSphere(made, layers);
    else if (mode === "tree") asTree(made, graph, layers);
    const missing = mode === "map" ? asMap(made, layers, addressOf) : 0;
    return { points: made, unlocated: missing };
  }, [graph, mode, addressOf]);

  // 引いたときに見せる、拠点ごとにまとめた形
  const clustered = useMemo(() => (graph ? clusterOf(points, graph.edges) : null), [points, graph]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d");
    if (!context) return;

    let running = true;
    const detail = {
      points,
      byId: new Map(points.map((p) => [p.id, p])),
      edges: (graph?.edges ?? []).filter(
        (edge) =>
          points.some((p) => p.id === edge.source) && points.some((p) => p.id === edge.target),
      ) as { id: number; source: number; target: number }[],
      counts: new Map<number, number>(),
    };
    const summary = clustered
      ? {
          points: clustered.points,
          byId: new Map(clustered.points.map((p) => [p.id, p])),
          edges: clustered.edges,
          counts: clustered.counts,
        }
      : detail;

    let packets: Packet[] = [];
    let showing = summary;
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

      // 引いているうちは拠点ごとの玉、寄ると中の機器が出る
      const wanted = zoom.current < CLUSTER_ZOOM ? summary : detail;
      if (wanted !== showing) {
        showing = wanted;
        packets = [];
      }
      const { points: shown, byId, edges, counts } = showing;
      // 地図は回さない。形が読めなくなる
      if (spin.current && settings.turning && !drag.current && mode !== "map") {
        angle.current.yaw += spin.current;
      }

      const { yaw, pitch } = angle.current;
      const cx = element.clientWidth / 2 + pan.current.x;
      const cy = element.clientHeight / 2 + pan.current.y;
      const distance = 900;
      const magnify = zoom.current;

      const project = (x: number, y: number, z: number) => {
        const x1 = x * Math.cos(yaw) - z * Math.sin(yaw);
        const z1 = x * Math.sin(yaw) + z * Math.cos(yaw);
        const y2 = y * Math.cos(pitch) - z1 * Math.sin(pitch);
        const z2 = y * Math.sin(pitch) + z1 * Math.cos(pitch);
        const scale = distance / (distance + z2 + 400);
        return { sx: cx + x1 * scale * magnify, sy: cy + y2 * scale * magnify, scale };
      };

      for (const point of shown) {
        // 止まっていると絵に見えるので、少しだけ揺らす
        const sway = settings.drift;
        const driftX = Math.sin(seconds * 0.5 + point.phase) * sway;
        const driftY = Math.sin(seconds * 0.35 + point.phase * 1.7) * sway * 0.8;
        const driftZ = Math.cos(seconds * 0.45 + point.phase) * sway;
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

      // 地図では輪郭と目盛りを薄く敷く。点だけだと地図に見えない
      if (mode === "map") {
        context.strokeStyle = "rgba(124, 196, 255, 0.07)";
        context.lineWidth = 1;
        for (let lon = BOUNDS.minLon; lon <= BOUNDS.maxLon; lon += 5) {
          const a = project(onMap(0, lon).x, 0, -350);
          const b = project(onMap(0, lon).x, 0, 350);
          context.beginPath();
          context.moveTo(a.sx, a.sy);
          context.lineTo(b.sx, b.sy);
          context.stroke();
        }
        for (let lat = BOUNDS.minLat; lat <= BOUNDS.maxLat; lat += 5) {
          const a = project(-450, 0, onMap(lat, 0).z);
          const b = project(450, 0, onMap(lat, 0).z);
          context.beginPath();
          context.moveTo(a.sx, a.sy);
          context.lineTo(b.sx, b.sy);
          context.stroke();
        }

        context.strokeStyle = "rgba(150, 210, 255, 0.5)";
        context.lineWidth = 1.2;
        for (const island of COASTLINE) {
          context.beginPath();
          island.forEach(([lon, lat], index) => {
            const spot = onMap(lat, lon);
            const screen = project(spot.x, 0, spot.z);
            if (index === 0) context.moveTo(screen.sx, screen.sy);
            else context.lineTo(screen.sx, screen.sy);
          });
          context.closePath();
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
        context.strokeStyle = `rgba(124, 196, 255, ${(0.12 + depth * 0.3) * settings.link})`;
        context.beginPath();
        context.moveTo(a.screenX, a.screenY);
        context.quadraticCurveTo(mid.x, mid.y, b.screenX, b.screenY);
        context.stroke();
      }

      // 光の玉。行き先はランダムで、行きと帰りの両方がある
      if (edges.length && packets.length < MAX_PACKETS && Math.random() < settings.flow) {
        const edge = edges[Math.floor(Math.random() * edges.length)];
        const forward = Math.random() < 0.5;
        packets.push({
          from: forward ? edge.source : edge.target,
          to: forward ? edge.target : edge.source,
          progress: 0,
          speed: (0.006 + Math.random() * 0.012) * settings.flowSpeed,
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

      const sorted = [...shown].sort((a, b) => a.scale - b.scale);
      // 地図では丸を小さくする。主役は線を流れる光の方
      const dot = (mode === "map" ? 1.0 : 2.4) * settings.dot;
      const grow = (mode === "map" ? 1.4 : 3.2) * settings.dot;
      for (const point of sorted) {
        const alert = settings.alerts ? ALERT_COLOR[point.severity] : undefined;
        // まとめた玉は、抱えている台数だけ大きくする
        const held = counts.get(point.id) ?? 0;
        const bulk = held ? 1 + Math.min(1.6, Math.log2(held + 1) * 0.45) : 1;
        const size = (dot + point.scale * grow) * (alert ? 1.5 : 1) * bulk;
        // 玉が届いた直後は明るくする（0.6 秒ほどで戻す）
        const lit = Math.max(0, 1 - (now - point.litAt) / 600);
        context.fillStyle = alert ?? TYPE_COLOR[point.deviceType] ?? TYPE_COLOR.other;
        // ふだんの機器は控えめに。要注意のものだけはっきり出す
        context.globalAlpha = alert ? 0.75 + point.scale * 0.25 : 0.2 + point.scale * 0.4;
        if (alert) {
          context.shadowColor = alert;
          context.shadowBlur = 10 * point.scale;
        } else if (held) {
          // 拠点の玉は、中にネットワークがあることが分かるように光らせる
          context.shadowColor = "rgba(150, 210, 255, 0.8)";
          context.shadowBlur = 8 + Math.min(14, held);
        }
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
        if (settings.labels && (held ? point.scale > 0.8 : point.scale > 0.95) && mode !== "map") {
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
  }, [points, clustered, graph, settings, mode]);

  return (
    <section>
      <h1>立体で眺める</h1>
      <p className="muted">
        全体の形をつかむための遊びのページです。流れている光はランダムに出しているだけで、
        実際の通信量ではありません。どこがどこに繋がっているかを読むのは
        <RouterLink to="/topology"> トポロジ図 </RouterLink>
        の方が向いています。ドラッグで回す、Shift か右ボタンのドラッグで位置をずらす、
        ホイールで寄る／引く、です。引いているうちは拠点ごとの玉にまとまり、寄るとその中の
        機器が出ます。
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
        <label className="vlan-chip" title="速さは覚えたまま、回転だけ止めます">
          <input
            type="checkbox"
            checked={settings.turning}
            onChange={(event) => set("turning", event.target.checked)}
            disabled={mode === "map"}
          />{" "}
          回す
        </label>
        <button onClick={() => (zoom.current = Math.min(4, zoom.current * 1.3))}>寄る ＋</button>
        <button onClick={() => (zoom.current = Math.max(0.3, zoom.current / 1.3))}>引く －</button>
        <button
          onClick={() => {
            pan.current = { x: 0, y: 0 };
            zoom.current = 1;
            angle.current = mode === "map" ? { yaw: 0, pitch: -1.15 } : { yaw: 0.6, pitch: -0.35 };
          }}
        >
          位置を戻す
        </button>
        <span className="muted">
          {graph ? `${graph.nodes.length}台 ${graph.edges.length}本` : "読み込み中…"}
          {mode === "map" && unlocated > 0 ? ` / 住所から位置が分からない機器 ${unlocated}台` : ""}
        </span>
      </div>

      <details className="space-settings">
        <summary>見え方を調整する</summary>
        <div className="space-controls">
          <Slider
            label="回転"
            hint="負の値で逆回り。図をドラッグで回すと、その向きと速さがここに入ります"
            min={-0.02}
            max={0.02}
            step={0.0002}
            value={settings.spin}
            onChange={(value) => set("spin", value)}
          />
          <Slider
            label="ゆれ"
            hint="放置中に機器が揺れる幅"
            min={0}
            max={20}
            step={1}
            value={settings.drift}
            onChange={(value) => set("drift", value)}
          />
          <Slider
            label="光の量"
            hint="線を流れる光の出やすさ。0 で出しません"
            min={0}
            max={1}
            step={0.05}
            value={settings.flow}
            onChange={(value) => set("flow", value)}
          />
          <Slider
            label="光の速さ"
            min={0.2}
            max={3}
            step={0.1}
            value={settings.flowSpeed}
            onChange={(value) => set("flowSpeed", value)}
          />
          <Slider
            label="機器の大きさ"
            min={0.3}
            max={3}
            step={0.1}
            value={settings.dot}
            onChange={(value) => set("dot", value)}
          />
          <Slider
            label="線の明るさ"
            min={0.2}
            max={3}
            step={0.1}
            value={settings.link}
            onChange={(value) => set("link", value)}
          />
          <label className="vlan-chip">
            <input
              type="checkbox"
              checked={settings.labels}
              onChange={(event) => set("labels", event.target.checked)}
            />{" "}
            手前の機器名を出す
          </label>
          <label className="vlan-chip" title="保守切れ・期限間近の機器を強い色にします">
            <input
              type="checkbox"
              checked={settings.alerts}
              onChange={(event) => set("alerts", event.target.checked)}
            />{" "}
            要注意を強調する
          </label>
          <button onClick={() => setSettings(DEFAULTS)}>既定に戻す</button>
        </div>
      </details>

      <canvas
        ref={canvas}
        className="space-canvas"
        onContextMenu={(event) => event.preventDefault()}
        onPointerDown={(event) => {
          // 右ボタンか Shift 併用で中心をずらす。ふつうのドラッグは回転
          drag.current = {
            x: event.clientX,
            y: event.clientY,
            pan: event.button === 2 || event.shiftKey,
            spin: 0,
          };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerUp={() => {
          // 最後に動かした向きと速さを、そのまま自動回転に渡す
          const released = drag.current;
          drag.current = null;
          if (released && !released.pan && released.spin) {
            set("spin", Math.max(-0.02, Math.min(0.02, released.spin)));
          }
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          const dx = event.clientX - drag.current.x;
          const dy = event.clientY - drag.current.y;
          if (drag.current.pan) {
            pan.current.x += dx;
            pan.current.y += dy;
          } else {
            if (mode !== "map") angle.current.yaw += dx * 0.008;
            angle.current.pitch += dy * 0.008;
            if (mode === "map") {
              // 地図は水平（0）から真上（-90度）までだけ。裏返すと読めない
              angle.current.pitch = Math.max(-Math.PI / 2, Math.min(0, angle.current.pitch));
            }
            // 手を離したあとも、回した向きにそのまま回り続ける
            drag.current.spin = dx * 0.0006;
          }
          drag.current = { ...drag.current, x: event.clientX, y: event.clientY };
        }}
        onWheel={(event) => {
          zoom.current = Math.max(0.3, Math.min(4, zoom.current * (event.deltaY < 0 ? 1.1 : 0.9)));
        }}
      />
    </section>
  );
}
