/**
 * 機器を囲む枠の位置。箱の外側に余白を取る。
 *
 * 枠は入れ子にできる。ラックの枠を、部屋やフロアの枠で更に囲むと、その機材が
 * どこに置いてあるのかが図だけで分かる。外側の枠は、中の枠を囲んだ外を回る。
 */

import type { Node } from "@xyflow/react";

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
  const width = (node: { width?: number }) => node.width ?? 190;
  const height = (node: { height?: number }) => node.height ?? 78;
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
        data.width ?? (typeof node.style?.width === "number" ? node.style.width : 190),
      height:
        data.height ?? (typeof node.style?.height === "number" ? node.style.height : 78),
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
    const box = boxes.get(data.groupId ?? Number(String(node.id).replace("g-", "")));
    if (!box) return node;
    return {
      ...node,
      position: { x: box.x, y: box.y },
      data: { ...node.data, width: box.width, height: box.height },
      style: { ...node.style, width: box.width, height: box.height },
    };
  });
}
