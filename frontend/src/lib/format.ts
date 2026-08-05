import type { DeviceStatus, DeviceType, Severity } from "../api/types";

export const SEVERITY_LABEL: Record<Severity, string> = {
  expired: "期限切れ",
  critical: "要対応",
  warning: "要計画",
  ok: "問題なし",
  unknown: "情報未登録",
};

export const DEVICE_TYPE_LABEL: Record<DeviceType, string> = {
  router: "ルータ",
  switch: "スイッチ",
  firewall: "ファイアウォール",
  load_balancer: "ロードバランサ",
  wireless_ap: "無線AP",
  wireless_controller: "無線コントローラ",
  server: "サーバ",
  other: "その他",
};

export const DEVICE_STATUS_LABEL: Record<DeviceStatus, string> = {
  active: "稼働中",
  standby: "予備機",
  maintenance: "保守作業中",
  retired: "撤去済み",
};

/** 残日数を「あと◯年◯ヶ月」「◯日超過」のような読みやすい表現にする。 */
export function formatRemaining(days: number | null): string {
  if (days === null) return "—";
  if (days < 0) return `${Math.abs(days).toLocaleString()}日 超過`;
  if (days === 0) return "本日まで";
  if (days < 60) return `あと${days}日`;

  const months = Math.floor(days / 30.44);
  if (months < 24) return `あと約${months}ヶ月`;
  return `あと約${(days / 365.25).toFixed(1)}年`;
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const [y, m, d] = value.split("-");
  return `${y}/${m}/${d}`;
}

export function formatCurrency(value: number | null): string {
  return value === null ? "—" : `¥${Math.round(value).toLocaleString()}`;
}
