import { BaseEdge, EdgeLabelRenderer, Position, type EdgeProps } from "@xyflow/react";

import { MEDIA_COLOR, formatPort, mediaOf, type PortStyle } from "../lib/portlabel";

export interface WireBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface PortEdgeData extends Record<string, unknown> {
  sourceInterface: string;
  targetInterface: string;
  /** 人が付けた線の名前。あればポート名より優先して中央に出す */
  manualLabel: string | null;
  portStyle: PortStyle;
  /** ポート名を線に沿わせて回転する */
  alongLine: boolean;
  /** ラベルを出さない（シンプル図で線だけにしたいとき） */
  hideLabels: boolean;
  /** 線を通す道のずらし幅。同じところから出る線が重ならないようにする */
  lane?: number;
  /** 束の中の順。0 が相手の並びに先頭 */
  laneRank?: number;
  laneCount?: number;
  warning: string | null;
  /** 自分以外の機器の箱。線が貫かないよう迂回する */
  obstacles?: WireBox[];
  sourceBox?: WireBox;
  targetBox?: WireBox;
}

/** ノード枠から、その先どのくらい離してラベルを置くか。 */
export const PORT_LABEL_GAP = 14;
/** 線に重ならないよう、線と垂直な向きへずらす幅。束の本数でさらに開く。 */
export const PORT_LABEL_SIDE = 12;

const DIRECTION: Record<Position, [number, number]> = {
  [Position.Top]: [0, -1],
  [Position.Bottom]: [0, 1],
  [Position.Left]: [-1, 0],
  [Position.Right]: [1, 0],
};

/**
 * ポート名ラベルの位置と、箱から見て外側への基準点（transform の原点）。
 *
 * translate(-50%,-50%) で中央寄せすると、文字の半分が自分の箱へ戻って隠れる。
 * 引き出し方向の手前側を基準にして、箱の外側にだけ伸ばす。
 */
export function portLabelPlacement(
  x: number,
  y: number,
  position: Position,
  gap: number = PORT_LABEL_GAP,
  side: number = PORT_LABEL_SIDE,
): { x: number; y: number; anchor: string } {
  const [dx, dy] = DIRECTION[position] ?? [0, 0];
  const px = x + dx * gap - dy * side;
  const py = y + dy * gap + dx * side;
  const anchor =
    position === Position.Right
      ? "0%, -50%"
      : position === Position.Left
        ? "-100%, -50%"
        : position === Position.Bottom
          ? "-50%, 0%"
          : "-50%, -100%";
  return { x: px, y: py, anchor };
}

/**
 * 縦横だけの通り道。構成図の定石どおり、箱の間で束ねてから相手の高さで抜く。
 * 曲がった SmoothStep は、束の順が崩れると交差して見える。
 */
export function orthogonalBusPath(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: "top" | "bottom" | "left" | "right";
  targetFace: "top" | "bottom" | "left" | "right";
  rank: number;
  count: number;
  gap?: number;
}): { path: string; labelX: number; labelY: number } | null {
  const gap = input.gap ?? 16;
  const inset = 28;
  const rank = Math.max(0, input.rank);
  const steps = Math.max(0, input.count - 1);
  const sx = input.sourceX;
  const sy = input.sourceY;
  const tx = input.targetX;
  const ty = input.targetY;
  // 接続点が相手の高さに寄っている前提。高さが揃えば横一直線。
  // ハブ側で一度重ねると折れが増えるだけなので、出口から扇に開く。
  if (input.sourceFace === "right" && input.targetFace === "left" && tx - sx > 40) {
    if (Math.abs(sy - ty) < 3) {
      return {
        path: `M ${sx},${sy} L ${tx},${ty}`,
        labelX: (sx + tx) / 2,
        labelY: sy,
      };
    }
    const usable = Math.max(0, tx - sx - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busX = tx - inset - rank * pitch;
    return {
      path: `M ${sx},${sy} L ${busX},${sy} L ${busX},${ty} L ${tx},${ty}`,
      labelX: busX,
      labelY: (sy + ty) / 2,
    };
  }
  if (input.sourceFace === "left" && input.targetFace === "right" && sx - tx > 40) {
    if (Math.abs(sy - ty) < 3) {
      return {
        path: `M ${sx},${sy} L ${tx},${ty}`,
        labelX: (sx + tx) / 2,
        labelY: sy,
      };
    }
    const usable = Math.max(0, sx - tx - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busX = tx + inset + rank * pitch;
    return {
      path: `M ${sx},${sy} L ${busX},${sy} L ${busX},${ty} L ${tx},${ty}`,
      labelX: busX,
      labelY: (sy + ty) / 2,
    };
  }
  if (input.sourceFace === "bottom" && input.targetFace === "top" && ty - sy > 40) {
    if (Math.abs(sx - tx) < 3) {
      return {
        path: `M ${sx},${sy} L ${tx},${ty}`,
        labelX: sx,
        labelY: (sy + ty) / 2,
      };
    }
    const usable = Math.max(0, ty - sy - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busY = ty - inset - rank * pitch;
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  if (input.sourceFace === "top" && input.targetFace === "bottom" && sy - ty > 40) {
    if (Math.abs(sx - tx) < 3) {
      return {
        path: `M ${sx},${sy} L ${tx},${ty}`,
        labelX: sx,
        labelY: (sy + ty) / 2,
      };
    }
    const usable = Math.max(0, sy - ty - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busY = ty + inset + rank * pitch;
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  // 同じ段で左右が塞がるとき、上下どちらかの辺から回す（2折れ）
  if (input.sourceFace === "bottom" && input.targetFace === "bottom") {
    const busY = Math.max(sy, ty) + inset + rank * (steps > 0 ? gap : 0);
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  if (input.sourceFace === "top" && input.targetFace === "top") {
    const busY = Math.min(sy, ty) - inset - rank * (steps > 0 ? gap : 0);
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  return null;
}

/** 上下が近いときは直交バスにしない、と同じで縦も一直線を許す。 */
function nearStraightVertical(
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  sourceFace: Face,
  targetFace: Face,
): { path: string; labelX: number; labelY: number } | null {
  if (Math.abs(sx - tx) >= 3) return null;
  if (sourceFace === "bottom" && targetFace === "top" && ty - sy > 40) {
    return { path: `M ${sx},${sy} L ${tx},${ty}`, labelX: sx, labelY: (sy + ty) / 2 };
  }
  if (sourceFace === "top" && targetFace === "bottom" && sy - ty > 40) {
    return { path: `M ${sx},${sy} L ${tx},${ty}`, labelX: sx, labelY: (sy + ty) / 2 };
  }
  return null;
}

type Face = "top" | "bottom" | "left" | "right";
type Pt = { x: number; y: number };

function pointsToPath(points: Pt[]): { path: string; labelX: number; labelY: number } {
  const cleaned: Pt[] = [points[0]];
  for (const point of points.slice(1)) {
    const last = cleaned[cleaned.length - 1];
    if (Math.abs(point.x - last.x) > 0.5 || Math.abs(point.y - last.y) > 0.5) {
      cleaned.push(point);
    }
  }
  const path = cleaned.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x},${p.y}`).join(" ");
  let bestLen = -1;
  let labelX = cleaned[0]?.x ?? 0;
  let labelY = cleaned[0]?.y ?? 0;
  for (let i = 0; i < cleaned.length - 1; i += 1) {
    const a = cleaned[i];
    const b = cleaned[i + 1];
    const len = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
    if (len > bestLen) {
      bestLen = len;
      labelX = (a.x + b.x) / 2;
      labelY = (a.y + b.y) / 2;
    }
  }
  return { path, labelX, labelY };
}

/** 箱の内側を通るか。辺上の接続は貫きにしない（輸出 SVG と同じ判定）。 */
export function segmentPiercesBox(box: WireBox, a: Pt, b: Pt): boolean {
  const pad = 4;
  const ix1 = box.x1 + pad;
  const iy1 = box.y1 + pad;
  const ix2 = box.x2 - pad;
  const iy2 = box.y2 - pad;
  if (ix2 <= ix1 || iy2 <= iy1) return false;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  let t0 = 0;
  let t1 = 1;
  const clips: Array<[number, number]> = [
    [-dx, a.x - ix1],
    [dx, ix2 - a.x],
    [-dy, a.y - iy1],
    [dy, iy2 - a.y],
  ];
  for (const [p, q] of clips) {
    if (Math.abs(p) < 1e-12) {
      if (q < 0) return false;
      continue;
    }
    const t = q / p;
    if (p < 0) t0 = Math.max(t0, t);
    else t1 = Math.min(t1, t);
    if (t0 > t1) return false;
  }
  return t1 - t0 > 1e-4 && t0 < 1 - 1e-9 && t1 > 1e-9;
}

export function routePierceCount(points: Pt[], boxes: WireBox[]): number {
  let hits = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    for (const box of boxes) {
      if (segmentPiercesBox(box, points[i], points[i + 1])) hits += 1;
    }
  }
  return hits;
}

function exitsOutward(points: Pt[], face: Face): boolean {
  if (points.length < 2) return false;
  const a = points[0];
  const b = points[1];
  if (face === "left") return b.x < a.x - 0.5;
  if (face === "right") return b.x > a.x + 0.5;
  if (face === "top") return b.y < a.y - 0.5;
  if (face === "bottom") return b.y > a.y + 0.5;
  return true;
}

function ensureOutward(points: Pt[], face: Face, gap = 16): Pt[] {
  if (points.length < 2 || exitsOutward(points, face)) return points;
  const a = points[0];
  const b = points[1];
  const mid =
    face === "left"
      ? { x: a.x - gap, y: a.y }
      : face === "right"
        ? { x: a.x + gap, y: a.y }
        : face === "top"
          ? { x: a.x, y: a.y - gap }
          : { x: a.x, y: a.y + gap };
  if (face === "left" || face === "right") {
    const corner = { x: mid.x, y: b.y };
    if (Math.abs(b.x - a.x) <= 0.5) {
      return [a, mid, corner, ...points.slice(2)];
    }
    return [a, mid, corner, ...points.slice(1)];
  }
  const corner = { x: b.x, y: mid.y };
  if (Math.abs(b.y - a.y) <= 0.5) {
    return [a, mid, corner, ...points.slice(2)];
  }
  return [a, mid, corner, ...points.slice(1)];
}

function pathLength(points: Pt[]): number {
  let len = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    len += Math.abs(points[i + 1].x - points[i].x) + Math.abs(points[i + 1].y - points[i].y);
  }
  return len;
}

function collapseCollinear(points: Pt[]): Pt[] {
  if (points.length < 3) return points;
  const out: Pt[] = [points[0]];
  for (let i = 1; i < points.length - 1; i += 1) {
    const a = out[out.length - 1];
    const b = points[i];
    const c = points[i + 1];
    if (Math.abs(b.x - a.x) <= 0.5 && Math.abs(b.y - a.y) <= 0.5) continue;
    const colinear =
      (Math.abs(a.x - b.x) <= 0.5 && Math.abs(b.x - c.x) <= 0.5) ||
      (Math.abs(a.y - b.y) <= 0.5 && Math.abs(b.y - c.y) <= 0.5);
    if (colinear) continue;
    out.push(b);
  }
  const last = points[points.length - 1];
  if (
    Math.abs(last.x - out[out.length - 1].x) > 0.5 ||
    Math.abs(last.y - out[out.length - 1].y) > 0.5
  ) {
    out.push(last);
  }
  return out.length > 1 ? out : points;
}

function parsePathPoints(path: string): Pt[] {
  return path
    .split(/[ML]\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [x, y] = part.split(",").map(Number);
      return { x, y };
    });
}

/** 同じ口のまま、上下のすき間へ迂回する。口を上下に付け替えると画面のハンドルと食い違う。 */
function detourKeepFaces(
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  sourceFace: Face,
  targetFace: Face,
  boxes: WireBox[],
  side: "above" | "below",
): Pt[] | null {
  if (
    !(
      (sourceFace === "left" || sourceFace === "right") &&
      (targetFace === "left" || targetFace === "right")
    )
  ) {
    return null;
  }
  const pad = 24;
  const out = 16;
  const ox = sx + (sourceFace === "right" ? out : sourceFace === "left" ? -out : 0);
  const ix = tx + (targetFace === "right" ? out : targetFace === "left" ? -out : 0);
  // 通り道の横幅に重なり、直進帯を塞ぐ箱だけ避ける
  const loX = Math.min(ox, ix);
  const hiX = Math.max(ox, ix);
  const bandLo = Math.min(sy, ty) - pad;
  const bandHi = Math.max(sy, ty) + pad;
  let channel = side === "below" ? Math.max(sy, ty) + pad : Math.min(sy, ty) - pad;
  for (const box of boxes) {
    if (box.x2 < loX - 1 || box.x1 > hiX + 1) continue;
    if (box.y2 < bandLo || box.y1 > bandHi) continue;
    if (side === "below") channel = Math.max(channel, box.y2 + pad);
    else channel = Math.min(channel, box.y1 - pad);
  }
  return [
    { x: sx, y: sy },
    { x: ox, y: sy },
    { x: ox, y: channel },
    { x: ix, y: channel },
    { x: ix, y: ty },
    { x: tx, y: ty },
  ];
}

export function facesForEdge(input: {
  sourceBox: WireBox;
  targetBox: WireBox;
  obstacles: WireBox[];
  sourceSide?: Face | null;
  targetSide?: Face | null;
}): { sourceFace: Face; targetFace: Face } {
  const mid = (box: WireBox) => ({
    x: (box.x1 + box.x2) / 2,
    y: (box.y1 + box.y2) / 2,
  });
  const srcMid = mid(input.sourceBox);
  const dstMid = mid(input.targetBox);
  // sideToward 相当（保存座標中心）
  const toward = (from: Pt, to: Pt): Face => {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    if (Math.abs(dy) < 60) return dx > 0 ? "right" : "left";
    if (Math.abs(dx) >= 400) return dx > 0 ? "right" : "left";
    if (Math.abs(dy) >= 100) return dy > 0 ? "bottom" : "top";
    return dy > 0 ? "bottom" : "top";
  };
  let sourceFace = input.sourceSide ?? toward(srcMid, dstMid);
  let targetFace = input.targetSide ?? toward(dstMid, srcMid);
  if (
    (sourceFace === "left" || sourceFace === "right") &&
    (targetFace === "left" || targetFace === "right")
  ) {
    const boxes = [input.sourceBox, input.targetBox, ...input.obstacles];
    const clear = (sf: Face, tf: Face) => {
      const start =
        sf === "right"
          ? { x: input.sourceBox.x2, y: srcMid.y }
          : sf === "left"
            ? { x: input.sourceBox.x1, y: srcMid.y }
            : sf === "bottom"
              ? { x: srcMid.x, y: input.sourceBox.y2 }
              : { x: srcMid.x, y: input.sourceBox.y1 };
      const end =
        tf === "right"
          ? { x: input.targetBox.x2, y: dstMid.y }
          : tf === "left"
            ? { x: input.targetBox.x1, y: dstMid.y }
            : tf === "bottom"
              ? { x: dstMid.x, y: input.targetBox.y2 }
              : { x: dstMid.x, y: input.targetBox.y1 };
      const bus = orthogonalBusPath({
        sourceX: start.x,
        sourceY: start.y,
        targetX: end.x,
        targetY: end.y,
        sourceFace: sf,
        targetFace: tf,
        rank: 0,
        count: 1,
      });
      if (!bus) return false;
      const points = bus.path
        .split(/[ML]\s*/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => {
          const [x, y] = part.split(",").map(Number);
          return { x, y };
        });
      return routePierceCount(points, boxes) === 0;
    };
    if (!clear(sourceFace, targetFace)) {
      const pairs: Array<[Face, Face]> = [
        ["bottom", "top"],
        ["top", "bottom"],
        ["bottom", "bottom"],
        ["top", "top"],
      ];
      for (const [sf, tf] of pairs) {
        if (clear(sf, tf)) {
          return { sourceFace: sf, targetFace: tf };
        }
      }
    }
  }
  return { sourceFace, targetFace };
}

/**
 * 画面の結線。バス → マンハッタン → 上下迂回を試し、箱を貫かないものを採る。
 */
export function routeWire(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: Face;
  targetFace: Face;
  rank: number;
  count: number;
  gap?: number;
  obstacles?: WireBox[];
  sourceBox?: WireBox;
  targetBox?: WireBox;
}): { path: string; labelX: number; labelY: number } {
  const pierceBoxes: WireBox[] = [
    ...(input.sourceBox ? [input.sourceBox] : []),
    ...(input.targetBox ? [input.targetBox] : []),
    ...(input.obstacles ?? []),
  ];

  const candidates: Pt[][] = [];
  const straight = nearStraightVertical(
    input.sourceX,
    input.sourceY,
    input.targetX,
    input.targetY,
    input.sourceFace,
    input.targetFace,
  );
  if (straight) candidates.push(parsePathPoints(straight.path));

  const bus = orthogonalBusPath(input);
  if (bus) candidates.push(parsePathPoints(bus.path));

  const out = 16;
  const ox =
    input.sourceX +
    (input.sourceFace === "right" ? out : input.sourceFace === "left" ? -out : 0);
  const oy =
    input.sourceY +
    (input.sourceFace === "bottom" ? out : input.sourceFace === "top" ? -out : 0);
  const ix =
    input.targetX +
    (input.targetFace === "right" ? out : input.targetFace === "left" ? -out : 0);
  const iy =
    input.targetY +
    (input.targetFace === "bottom" ? out : input.targetFace === "top" ? -out : 0);
  const horizontalFirst = input.sourceFace === "left" || input.sourceFace === "right";
  const midX = horizontalFirst ? ox : ix;
  const midY = horizontalFirst ? iy : oy;
  candidates.push([
    { x: input.sourceX, y: input.sourceY },
    { x: ox, y: oy },
    { x: midX, y: midY },
    { x: ix, y: iy },
    { x: input.targetX, y: input.targetY },
  ]);

  if (pierceBoxes.length > 0) {
    for (const side of ["below", "above"] as const) {
      const detour = detourKeepFaces(
        input.sourceX,
        input.sourceY,
        input.targetX,
        input.targetY,
        input.sourceFace,
        input.targetFace,
        pierceBoxes,
        side,
      );
      if (detour) candidates.push(detour);
    }
  }

  const scored = candidates
    .map((raw) => collapseCollinear(ensureOutward(raw, input.sourceFace)))
    .map((points) => ({
      points,
      pierce: routePierceCount(points, pierceBoxes),
      outward: exitsOutward(points, input.sourceFace) ? 0 : 1,
      bends: Math.max(0, points.length - 2),
      length: pathLength(points),
    }));
  scored.sort(
    (a, b) =>
      a.pierce - b.pierce ||
      a.outward - b.outward ||
      a.bends - b.bends ||
      a.length - b.length,
  );
  const best = scored[0]?.points;
  if (!best || best.length < 2) {
    return manhattanRouteFallback(input);
  }
  return pointsToPath(best);
}

function manhattanRouteFallback(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: Face;
  targetFace: Face;
  rank: number;
  count: number;
  gap?: number;
}): { path: string; labelX: number; labelY: number } {
  const bus = orthogonalBusPath(input);
  if (bus) return bus;
  const out = 16;
  const sx =
    input.sourceX + (input.sourceFace === "right" ? out : input.sourceFace === "left" ? -out : 0);
  const sy =
    input.sourceY + (input.sourceFace === "bottom" ? out : input.sourceFace === "top" ? -out : 0);
  const tx =
    input.targetX + (input.targetFace === "right" ? out : input.targetFace === "left" ? -out : 0);
  const ty =
    input.targetY + (input.targetFace === "bottom" ? out : input.targetFace === "top" ? -out : 0);
  const horizontalFirst = input.sourceFace === "left" || input.sourceFace === "right";
  const midX = horizontalFirst ? sx : tx;
  const midY = horizontalFirst ? ty : sy;
  return {
    path: `M ${input.sourceX},${input.sourceY} L ${sx},${sy} L ${midX},${midY} L ${tx},${ty} L ${input.targetX},${input.targetY}`,
    labelX: (sx + tx) / 2,
    labelY: (sy + ty) / 2,
  };
}

/** どの向きの引き出しでも縦横だけ。バスが使えないときも曲線にしない。 */
export function manhattanRoute(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: "top" | "bottom" | "left" | "right";
  targetFace: "top" | "bottom" | "left" | "right";
  rank: number;
  count: number;
  gap?: number;
  obstacles?: WireBox[];
  sourceBox?: WireBox;
  targetBox?: WireBox;
}): { path: string; labelX: number; labelY: number } {
  return routeWire(input);
}

const FACE_OF: Record<Position, "top" | "bottom" | "left" | "right"> = {
  [Position.Top]: "top",
  [Position.Bottom]: "bottom",
  [Position.Left]: "left",
  [Position.Right]: "right",
};

/**
 * 結線1本。ポート名をそれぞれの機器側に置く。
 *
 * 1本の線に2つのポート名を並べると「左が上位」のような規則を覚える必要が出る。
 * 図の向きが変わればその規則は成り立たず、逆に読んでしまう。
 * ポートの隣にあれば規則は要らない。
 */
export default function PortLabeledEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data,
}: EdgeProps) {
  const info = (data ?? {}) as Partial<PortEdgeData>;
  const routed = routeWire({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourceFace: FACE_OF[sourcePosition],
    targetFace: FACE_OF[targetPosition],
    rank: info.laneRank ?? 0,
    count: info.laneCount ?? 1,
    obstacles: info.obstacles,
    sourceBox: info.sourceBox,
    targetBox: info.targetBox,
  });
  const path = routed.path;
  const labelX = routed.labelX;
  const labelY = routed.labelY;
  const angle = (Math.atan2(targetY - sourceY, targetX - sourceX) * 180) / Math.PI;

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {!info.hideLabels && (
        <EdgeLabelRenderer>
          {info.manualLabel ? (
            <div
              className="topo-edge-label center"
              style={{
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
            >
              {info.manualLabel}
            </div>
          ) : (
            <>
              <PortLabel
                name={info.sourceInterface ?? ""}
                x={sourceX}
                y={sourceY}
                position={sourcePosition}
                angle={angle}
                style={info.portStyle ?? "short"}
                alongLine={info.alongLine ?? false}
                opacity={style?.opacity}
                color={typeof style?.stroke === "string" ? style.stroke : undefined}
                rank={info.laneRank ?? 0}
              />
              <PortLabel
                name={info.targetInterface ?? ""}
                x={targetX}
                y={targetY}
                position={targetPosition}
                // 相手側から見た向き。ずらす向きが反転して、2つのラベルが
                // 線の反対側に出るので、機器が近くても重ならない
                angle={angle + 180}
                style={info.portStyle ?? "short"}
                alongLine={info.alongLine ?? false}
                opacity={style?.opacity}
                color={typeof style?.stroke === "string" ? style.stroke : undefined}
                rank={info.laneRank ?? 0}
              />
            </>
          )}
          {info.warning && (
            <div
              className="topo-edge-label warning"
              style={{
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
            >
              ⚠ {info.warning}
            </div>
          )}
        </EdgeLabelRenderer>
      )}
    </>
  );
}

function PortLabel({
  name,
  x,
  y,
  position,
  angle,
  style,
  alongLine,
  opacity,
  color,
  rank = 0,
}: {
  name: string;
  x: number;
  y: number;
  position: Position;
  angle: number;
  style: PortStyle;
  alongLine: boolean;
  opacity?: number | string;
  color?: string;
  /** 同じ辺の束の順。ポート名を線と垂直方向へずらして被りを減らす */
  rank?: number;
}) {
  if (!name) return null;

  const placed = portLabelPlacement(
    x,
    y,
    position,
    PORT_LABEL_GAP + Math.min(rank, 4) * 3,
    // 偶数・奇数で線の上下（左右）へ振り、束のポート名が同じ帯に積もらない
    (rank % 2 === 0 ? 1 : -1) * (PORT_LABEL_SIDE + Math.floor(rank / 2) * 14),
  );
  const normalized = ((angle + 180) % 360) - 180;
  const flipped = normalized > 90 || normalized <= -90;
  // 上下逆さまの文字は読めないので、半回転させて向きを揃える
  const rotation = alongLine ? (flipped ? normalized + (normalized > 0 ? -180 : 180) : normalized) : 0;

  return (
    <div
      className="topo-edge-label port"
      style={{
        transform: `translate(${placed.anchor}) translate(${placed.x}px, ${placed.y}px) rotate(${rotation}deg)`,
        color: color ?? MEDIA_COLOR[mediaOf(name)],
        ...(opacity ? { opacity } : {}),
      }}
      title={name}
    >
      {formatPort(name, style)}
    </div>
  );
}
