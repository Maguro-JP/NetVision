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

const SITE_FRAME_COLORS = ["#2f4d6a", "#4a7a58", "#8a6a3a", "#5a7a9a"];

type SiteFrameMember = {
  id: string;
  position: { x: number; y: number };
  data: {
    width?: number;
    siteId?: number | null;
    siteName?: string | null;
    outside?: boolean;
  };
};

/** 保存枠が無いとき、見えている拠点ごとに破線の枠を仮置きする。 */
export function framesFromSites(devices: SiteFrameMember[]): Node[] {
  const buckets = new Map<string, { label: string; members: SiteFrameMember[] }>();
  for (const device of devices) {
    const siteId = device.data.siteId;
    const name = device.data.siteName?.trim();
    const key =
      siteId != null ? `id:${siteId}` : name ? `name:${name}` : `solo:${device.id}`;
    const label = name || (device.data.outside ? "対向" : "この拠点");
    const bucket = buckets.get(key) ?? { label, members: [] };
    bucket.members.push(device);
    buckets.set(key, bucket);
  }
  if (buckets.size < 2) return [];

  return [...buckets.values()].map((bucket, index) => {
    const box = groupBox(
      bucket.members.map((node) => ({
        x: node.position.x,
        y: node.position.y,
        width: node.data.width,
      })),
    );
    const color = SITE_FRAME_COLORS[index % SITE_FRAME_COLORS.length];
    return {
      id: `g-site-${index}`,
      type: "group" as const,
      position: { x: box?.x ?? 0, y: box?.y ?? 0 },
      zIndex: 0,
      selectable: false,
      draggable: false,
      connectable: false,
      data: {
        label: bucket.label,
        plane: "custom",
        color,
        width: box?.width ?? 0,
        height: box?.height ?? 0,
        groupId: -1 - index,
        deviceIds: bucket.members.map((node) => Number(node.id)),
      },
    };
  });
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
