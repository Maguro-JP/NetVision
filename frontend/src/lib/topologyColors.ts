/**
 * 構成図の色。種別の枠と線の色は、人が現場の凡例に合わせられるようにする。
 */

import type { DeviceType } from "../api/types";
import { MEDIA_COLOR, type Media } from "./portlabel";

export const DEFAULT_TYPE_COLOR: Record<DeviceType, string> = {
  router: "#2f4d6a",
  switch: "#4a7a58",
  firewall: "#8a4a3a",
  load_balancer: "#5a4a8a",
  wireless_ap: "#3a6a8a",
  wireless_controller: "#3a6a8a",
  server: "#5a5a5a",
  other: "#6a655c",
};

export const PLANE_LABEL: Record<string, string> = {
  backbone: "コア網",
  metro: "メトロ／集約",
  access: "アクセス",
  custom: "任意",
};

const COLOR_KEY = "netvision.topology.colors";

export interface TopologyColors {
  types: Record<DeviceType, string>;
  media: Record<Media, string>;
}

export function defaultColors(): TopologyColors {
  return {
    types: { ...DEFAULT_TYPE_COLOR },
    media: { ...MEDIA_COLOR },
  };
}

export function loadColors(): TopologyColors {
  const fallback = defaultColors();
  try {
    const saved = JSON.parse(localStorage.getItem(COLOR_KEY) ?? "{}");
    return {
      types: { ...fallback.types, ...(saved.types ?? {}) },
      media: { ...fallback.media, ...(saved.media ?? {}) },
    };
  } catch {
    return fallback;
  }
}

export function saveColors(colors: TopologyColors): void {
  localStorage.setItem(COLOR_KEY, JSON.stringify(colors));
}

/** <input type="color"> は #rrggbb だけ受け付ける。 */
export function asHexColor(value: string, fallback = "#5a7a9a"): string {
  const match = /^#([0-9a-fA-F]{6})/.exec(value.trim());
  return match ? `#${match[1]}` : fallback;
}
