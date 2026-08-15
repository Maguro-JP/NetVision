/**
 * 機器を囲む枠の位置。箱の外側に余白を取る。
 */

import type { Node } from "@xyflow/react";

export const GROUP_PAD = 22;
export const GROUP_HEAD = 22;

export function groupBox(
  members: { x: number; y: number; width?: number; height?: number }[],
): { x: number; y: number; width: number; height: number } | null {
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

/** 機器を動かしたあと、囲み枠を箱の外側へ追従させる。 */
export function updateGroupBounds<T extends Node>(nodes: T[]): T[] {
  const devices = nodes.filter((node) => node.type !== "group");
  return nodes.map((node) => {
    if (node.type !== "group") return node;
    const deviceIds = ((node.data.deviceIds as number[] | undefined) ?? []).map(String);
    const members = devices.filter((item) => deviceIds.includes(item.id));
    const box = groupBox(
      members.map((item) => ({
        x: item.position.x,
        y: item.position.y,
        width:
          (item.data as { width?: number }).width ??
          (typeof item.style?.width === "number" ? item.style.width : undefined),
      })),
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
