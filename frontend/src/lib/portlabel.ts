/**
 * ポート名を図に載せる形に整える。
 *
 * サーバ側（`app/services/portlabel.py`）と同じ規則。画面と SVG 出力で
 * ポート名の見え方が変わると、同じ図のはずなのに別物に見えるため揃えてある。
 *
 * 種別（速度）は色で表せるので、文字は番号だけに削れる。
 * `Te1/1/1` の `Te` を色に移せば、残るのは `1/1/1` だけになる。
 */

export type Media =
  | "fast"
  | "giga"
  | "ten"
  | "forty"
  | "hundred"
  | "lag"
  | "virtual"
  | "other";

export const MEDIA_LABEL: Record<Media, string> = {
  fast: "100M",
  giga: "1G",
  ten: "10G",
  forty: "25G/40G",
  hundred: "100G",
  lag: "論理集約",
  virtual: "仮想",
  other: "その他",
};

/** 暗い画面でも白地の書き出しでも読める濃さに合わせる。 */
export const MEDIA_COLOR: Record<Media, string> = {
  fast: "#9aa4b0",
  giga: "#5aa9f8",
  ten: "#4cc38a",
  forty: "#e0a44a",
  hundred: "#c084fc",
  lag: "#f0776b",
  virtual: "#8b98a5",
  other: "#8b98a5",
};

export type PortStyle = "full" | "short" | "number";

const PREFIXES: [string, string, Media][] = [
  ["tengigabitethernet", "Te", "ten"],
  ["ten-gigabitethernet", "Te", "ten"],
  ["twentyfivegige", "Twe", "forty"],
  ["fortygigabitethernet", "Fo", "forty"],
  ["hundredgige", "Hu", "hundred"],
  ["gigabitethernet", "Gi", "giga"],
  // NEC IX は GigaEthernet（bit が無い）。Cisco と紛らわしいので別に持つ
  ["gigaethernet", "GE", "giga"],
  ["fastethernet", "Fa", "fast"],
  ["bridge-aggregation", "BAGG", "lag"],
  ["route-aggregation", "RAGG", "lag"],
  ["port-channel", "Po", "lag"],
  ["m-gigabitethernet", "MGi", "giga"],
  ["management", "Mgmt", "other"],
  ["loopback", "Lo", "virtual"],
  ["tunnel", "Tu", "virtual"],
  ["vlan-interface", "Vlan", "virtual"],
  ["vlan", "Vlan", "virtual"],
  ["xgigabitethernet", "XGE", "ten"],
  ["ethernet", "Eth", "other"],
];

/** Juniper 系は接頭辞そのものが種別を表す。 */
const JUNIPER: Record<string, [string, Media]> = {
  fe: ["fe", "fast"],
  ge: ["ge", "giga"],
  xe: ["xe", "ten"],
  et: ["et", "hundred"],
  ae: ["ae", "lag"],
  irb: ["irb", "virtual"],
  lo: ["lo", "virtual"],
  em: ["em", "other"],
  fxp: ["fxp", "other"],
};

const SPLIT = /^([A-Za-z][A-Za-z-]*?)\s*([\d/.:].*)?$/;
const JUNIPER_SPLIT = /^([a-z]+)-(\d.*)$/;

export interface PortParts {
  short: string;
  number: string;
  media: Media;
}

/** ポート名を（略記, 番号, 種別）に分ける。読めない名前は丸ごと残す。 */
export function splitPort(name: string): PortParts {
  const text = (name ?? "").trim();
  if (!text) return { short: "", number: "", media: "other" };

  const juniper = JUNIPER_SPLIT.exec(text);
  if (juniper && JUNIPER[juniper[1].toLowerCase()]) {
    const [short, media] = JUNIPER[juniper[1].toLowerCase()];
    return { short, number: juniper[2], media };
  }

  const match = SPLIT.exec(text.replace(/\s/g, ""));
  if (!match) return { short: text, number: "", media: "other" };

  const prefix = match[1].toLowerCase();
  const number = match[2] ?? "";

  for (const [candidate, short, media] of PREFIXES) {
    if (prefix === candidate) return { short, number, media };
  }
  // 既に略記で書かれている場合（Gi1/0/1 など）
  for (const [, short, media] of PREFIXES) {
    if (prefix === short.toLowerCase()) return { short, number, media };
  }
  // Juniper はハイフン無しで書かれることもある（ae0 / lo0）。
  // 小文字のときだけ見る（Cisco の Lo0 などを取り違えないため）
  if (match[1] === match[1].toLowerCase() && JUNIPER[prefix]) {
    const [short, media] = JUNIPER[prefix];
    return { short, number, media };
  }

  return { short: match[1], number, media: "other" };
}

export function mediaOf(name: string): Media {
  return splitPort(name).media;
}

/**
 * 図に載せる文字列にする。
 *
 * 種別が判別できた名前でしか番号だけにしない。 `port1` の `port` を削って
 * `1` にすると、色にも情報が乗らないまま元の名前が消える。
 * 削るのは「色に移した分だけ」でなければならない。
 */
export function formatPort(name: string, style: PortStyle = "short"): string {
  if (!name) return "";
  if (style === "full") return name;

  const { short, number, media } = splitPort(name);
  if (style === "number" && number && media !== "other") return number;
  return number ? `${short}${number}` : short || name;
}
