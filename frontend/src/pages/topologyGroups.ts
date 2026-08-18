/**
 * 機器を囲む枠の位置。箱の外側に余白を取る。
 *
 * 枠は入れ子にできる。ラックの枠を、部屋やフロアの枠で更に囲むと、その機材が
 * どこに置いてあるのかが図だけで分かる。外側の枠は、中の枠を囲んだ外を回る。
 */

import type { Node } from "@xyflow/react";

import { DEFAULT_HEIGHT, DEFAULT_WIDTH } from "./TopologyPage";

export const GROUP_PAD = 22;
export const GROUP_HEAD = 22;

export interface GroupBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function groupBox(
  members: { x: number; y: number; width?: number; height?: number }[],
): GroupBox | null {
  if (members.length === 0) return null;
  // 大きさが渡されていない機器は、画面の既定の箱として数える。
  // ここに古い数を置いておくと、枠だけが小さくなって中身がはみ出す
  const width = (node: { width?: number }) => node.width ?? DEFAULT_WIDTH;
  const height = (node: { height?: number }) => node.height ?? DEFAULT_HEIGHT;
  const left = Math.min(...members.map((node) => node.x));
  const top = Math.min(...members.map((node) => node.y));
  const right = Math.max(...members.map((node) => node.x + width(node)));
  const bottom = Math.max(...members.map((node) => node.y + height(node)));
  return {
    x: left - GROUP_PAD,
    y: top - GROUP_PAD - GROUP_HEAD,
    width: right - left + GROUP_PAD * 2,
    height: bottom - top + GROUP_PAD * 2 + GROUP_HEAD,
  };
}

export interface GroupSpec {
  id: number;
  parentId: number | null;
  deviceIds: number[];
}

/**
 * 枠ごとの大きさ。内側から決めて、外側はその外を回る。
 *
 * フロアの枠は自分では機器を持たないことがある。中の枠を数に入れないと、
 * 大きさが決まらず図から消えてしまう。
 */
export function groupBoxes(
  specs: GroupSpec[],
  deviceBox: Map<number, GroupBox>,
): Map<number, GroupBox> {
  const children = new Map<number | null, GroupSpec[]>();
  for (const spec of specs) {
    const key = spec.parentId;
    (children.get(key) ?? children.set(key, []).get(key)!).push(spec);
  }
  const out = new Map<number, GroupBox>();
  const busy = new Set<number>();

  const boxOf = (spec: GroupSpec): GroupBox | null => {
    const done = out.get(spec.id);
    if (done) return done;
    // 枠が輪になっていても図は出す。止まらなくなる方が困る
    if (busy.has(spec.id)) return null;
    busy.add(spec.id);
    const parts: GroupBox[] = [];
    for (const deviceId of spec.deviceIds) {
      const box = deviceBox.get(deviceId);
      if (box) parts.push(box);
    }
    for (const child of children.get(spec.id) ?? []) {
      const inner = boxOf(child);
      if (inner) parts.push(inner);
    }
    busy.delete(spec.id);
    const box = groupBox(parts);
    if (box) out.set(spec.id, box);
    return box;
  };

  for (const spec of specs) boxOf(spec);
  return out;
}

/**
 * 図の重なりの順（手前ほど大きい）。
 *
 * 小さいものが大きいものの下に入ると、掴めなくなって作業できない。
 * 拠点や建物のような大きい枠を一番後ろに置き、部屋、ラックと内側へ行くほど
 * 手前にする。線はその上、機器は一番手前。
 */
export const LAYER = {
  /** 枠。ここに枠の深さを足す（外側 0、内側ほど手前） */
  frame: 0,
  /** 図の外の相手。線に隠れない位置だが、枠より手前 */
  outside: 80,
  /** 結線 */
  edge: 100,
  /** 機器。掴めなくなると困るので一番手前 */
  device: 200,
} as const;

/** その枠の重なりの順。深いほど（内側ほど）手前に来る。 */
export function frameLayer(depth: number): number {
  return LAYER.frame + depth;
}

/** 枠の深さ。0 が一番外側。外側から順に描かないと、内側の名前が隠れる。 */
export function groupDepth(specs: GroupSpec[], id: number): number {
  const parentOf = new Map(specs.map((spec) => [spec.id, spec.parentId]));
  let depth = 0;
  let at = parentOf.get(id) ?? null;
  const seen = new Set<number>([id]);
  while (at !== null && !seen.has(at)) {
    seen.add(at);
    depth += 1;
    at = parentOf.get(at) ?? null;
  }
  return depth;
}

/** 機器を動かしたあと、囲み枠を箱の外側へ追従させる。 */
export function updateGroupBounds<T extends Node>(nodes: T[]): T[] {
  const deviceBox = new Map<number, GroupBox>();
  for (const node of nodes) {
    if (node.type === "group") continue;
    const data = node.data as { width?: number; height?: number };
    deviceBox.set(Number(node.id), {
      x: node.position.x,
      y: node.position.y,
      width:
        data.width ??
        (typeof node.style?.width === "number"
          ? node.style.width
          : DEFAULT_WIDTH),
      height:
        data.height ??
        (typeof node.style?.height === "number"
          ? node.style.height
          : DEFAULT_HEIGHT),
    });
  }
  const specs: GroupSpec[] = nodes
    .filter((node) => node.type === "group")
    .map((node) => {
      const data = node.data as {
        groupId?: number;
        parentId?: number | null;
        deviceIds?: number[];
      };
      return {
        id: data.groupId ?? Number(String(node.id).replace("g-", "")),
        parentId: data.parentId ?? null,
        deviceIds: data.deviceIds ?? [],
      };
    });
  const boxes = groupBoxes(specs, deviceBox);

  return nodes.map((node) => {
    if (node.type !== "group") return node;
    const data = node.data as { groupId?: number };
    const box = boxes.get(
      data.groupId ?? Number(String(node.id).replace("g-", "")),
    );
    if (!box) return node;
    return {
      ...node,
      position: { x: box.x, y: box.y },
      data: { ...node.data, width: box.width, height: box.height },
      style: { ...node.style, width: box.width, height: box.height },
    };
  });
}
