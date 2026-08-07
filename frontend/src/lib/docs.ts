import type { DocumentType, TemplateCategory } from "../api/types";

export const DOC_TYPE_LABEL: Record<DocumentType, string> = {
  command_reference: "コマンドリファレンス",
  config_guide: "コンフィグガイド",
  release_notes: "リリースノート",
  install_guide: "設置・導入ガイド",
  hardware_guide: "ハードウェアガイド",
  security_advisory: "セキュリティアドバイザリ",
  datasheet: "データシート",
  other: "その他",
};

export const TEMPLATE_CATEGORY_LABEL: Record<TemplateCategory, string> = {
  interface: "インターフェース",
  vlan: "VLAN",
  routing: "ルーティング",
  acl: "ACL",
  qos: "QoS",
  management: "管理系（SNMP/NTP/Syslog）",
  security: "セキュリティ",
  other: "その他",
};

/** 適用バージョン範囲を1行で表す。 */
export function versionRange(
  from: string | null,
  to: string | null,
  label: string | null,
): string {
  if (label) return label;
  if (!from && !to) return "全バージョン共通";
  if (from && to) return `${from} 〜 ${to}`;
  return from ? `${from} 以降` : `${to} 以前`;
}

export function formatBytes(size: number | null): string {
  if (size === null) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
