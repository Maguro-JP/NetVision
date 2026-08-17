import type { DeviceStatus, DeviceType, Severity } from "../api/types";

export const SEVERITY_LABEL: Record<Severity, string> = {
  expired: "期限切れ",
  critical: "要対応",
  warning: "要計画",
  ok: "問題なし",
  unknown: "情報未登録",
};

export const DEVICE_TYPE_LABEL: Record<DeviceType, string> = {
  router: "ルーター",
  switch: "スイッチ",
  firewall: "ファイアウォール",
  load_balancer: "ロードバランサー",
  wireless_ap: "無線AP",
  wireless_controller: "無線コントローラー",
  server: "サーバー",
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

/**
 * 日時を、その端末の時刻で表示する。
 *
 * API はタイムゾーン付き（UTC）で返すので、変換はブラウザに任せる。
 * タイムゾーンの付かない文字列を渡されると現地時刻と解釈されてずれるため、
 * 付いていない場合は UTC として補う。
 */
export function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const [y, m, d] = value.split("-");
  return `${y}/${m}/${d}`;
}

export function formatCurrency(value: number | null): string {
  return value === null ? "—" : `¥${Math.round(value).toLocaleString()}`;
}
