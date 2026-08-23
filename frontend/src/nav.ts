/**
 * メニューの並び。
 *
 * 機能が増えるたびにメニューへ1行足していたら、28行になった。上から順に
 * 読まないと目的の画面が見つからず、名前を覚えていない人には探せない。
 *
 * そこで2階層にする。左に出すのは「何をしに来たか」の9つだけで、その中の
 * 個別の画面は、開いたページの上にタブとして出す。左が短くなるだけでなく、
 * 近い画面どうしが同じ場所に並ぶので、隣に何があるのかも分かる。
 *
 * URL は変えていない。画面の名前で覚えている人、ブックマークしている人、
 * 別ウィンドウで開いている人がいる。並べ替えのために URL を動かすと、
 * それが全部切れる。切る価値のある変更ではない。
 */

export type NavItem = { to: string; label: string };

export type NavSection = {
  label: string;
  /** 左のメニューを押したときに開く画面。そのまとまりの入口 */
  to: string;
  /** 中の画面。1つだけのまとまり（ダッシュボードなど）では空にする */
  items: NavItem[];
  /** 左のメニューでの補足。名前だけでは何をする所か分からないものに付ける */
  hint?: string;
};

export const SECTIONS: NavSection[] = [
  { label: "ダッシュボード", to: "/dashboard", items: [] },
  { label: "トポロジー図", to: "/topology", items: [] },
  // /space（立体で眺める）と /hidden は隠し機能。URL 直打ちだけで出す
  {
    label: "台帳",
    to: "/devices",
    hint: "機器・拠点・VLAN",
    items: [
      { to: "/devices", label: "機器一覧" },
      { to: "/sites", label: "拠点" },
      { to: "/vlans", label: "VLAN 台帳" },
      { to: "/changes", label: "変更履歴" },
    ],
  },
  {
    label: "Config",
    to: "/configs",
    hint: "取り込み・点検・生成",
    items: [
      { to: "/configs", label: "取り込み" },
      { to: "/config-check", label: "見てもらう" },
      { to: "/config-plans", label: "計画（差分生成）" },
      { to: "/config-templates", label: "テンプレート" },
    ],
  },
  {
    label: "セキュリティ",
    to: "/exposures",
    hint: "脆弱性と対処",
    items: [
      { to: "/exposures", label: "対処状況" },
      { to: "/vulnerabilities", label: "脆弱性" },
      { to: "/remediation", label: "脆弱性への対処" },
    ],
  },
  {
    label: "更改・計画",
    to: "/replacement",
    hint: "期限・シナリオ・指摘",
    items: [
      { to: "/replacement", label: "更改支援" },
      { to: "/insights", label: "最適化の指摘" },
      { to: "/scenarios", label: "将来構成シナリオ" },
      { to: "/blueprint", label: "構成を起こす" },
    ],
  },
  {
    label: "調べる・頼む",
    to: "/ask",
    hint: "ことば・ログ・版",
    items: [
      { to: "/ask", label: "ことばで頼む" },
      { to: "/pipeline", label: "頼んで、通り道を見る" },
      { to: "/syslog", label: "ログを読む" },
      { to: "/calc", label: "数えれば分かること" },
      { to: "/os", label: "版と、いまの状態" },
    ],
  },
  {
    label: "取り込み・資料",
    to: "/import",
    hint: "台帳・EoL・資料",
    items: [
      { to: "/import", label: "台帳の取り込み" },
      { to: "/reference", label: "製品情報（EoL・脆弱性）" },
      { to: "/documents", label: "メーカー資料" },
    ],
  },
  {
    label: "困ったとき",
    to: "/glossary",
    hint: "言葉、できること",
    items: [
      { to: "/glossary", label: "言葉の説明" },
      { to: "/capabilities", label: "対応している機能" },
    ],
  },
];

/**
 * いま開いている画面が、どのまとまりの中にいるか。
 *
 * 1台の詳細（/devices/12）のような奥のページからも、上のタブが消えないように
 * 前方一致で見る。一致が複数あるときは長いほうを採る（/config-plans が
 * /configs に吸われないようにするため）。
 */
export function sectionFor(pathname: string): NavSection | undefined {
  let best: { section: NavSection; length: number } | undefined;
  for (const section of SECTIONS) {
    for (const path of [section.to, ...section.items.map((item) => item.to)]) {
      if (pathname !== path && !pathname.startsWith(`${path}/`)) continue;
      if (!best || path.length > best.length) best = { section, length: path.length };
    }
  }
  return best?.section;
}

/** そのタブが「いま開いている画面」か。奥のページでも親のタブを光らせる。 */
export function isCurrent(pathname: string, to: string): boolean {
  if (pathname === to) return true;
  if (!pathname.startsWith(`${to}/`)) return false;
  // 奥のページが、より近いタブに属しているなら、そちらに譲る
  const section = sectionFor(pathname);
  const closer = section?.items.some(
    (item) => item.to !== to && item.to.length > to.length && pathname.startsWith(item.to),
  );
  return !closer;
}
