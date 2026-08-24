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
  { label: "概況", to: "/dashboard", items: [] },
  { label: "構成図", to: "/topology", items: [] },
  // /space（立体で眺める）と /hidden は隠し機能。URL 直打ちだけで出す
  {
    label: "台帳",
    to: "/devices",
    hint: "機器・拠点・VLAN",
    items: [
      { to: "/devices", label: "機器" },
      { to: "/sites", label: "拠点" },
      { to: "/vlans", label: "VLAN" },
      { to: "/changes", label: "変更履歴" },
    ],
  },
  {
    label: "Config",
    to: "/configs",
    hint: "取り込み・点検・作成",
    items: [
      { to: "/configs", label: "取り込み" },
      { to: "/config-check", label: "点検" },
      { to: "/config-plans", label: "変更計画" },
      { to: "/config-templates", label: "テンプレート" },
    ],
  },
  {
    label: "セキュリティ",
    to: "/exposures",
    hint: "脆弱性と対処",
    items: [
      { to: "/exposures", label: "対処状況" },
      { to: "/vulnerabilities", label: "脆弱性一覧" },
      { to: "/remediation", label: "対処手順" },
    ],
  },
  {
    label: "更改",
    to: "/replacement",
    hint: "期限・シナリオ・改善",
    items: [
      { to: "/replacement", label: "更改の見通し" },
      { to: "/insights", label: "改善の指摘" },
      { to: "/scenarios", label: "構成シナリオ" },
      { to: "/blueprint", label: "構成の下書き" },
    ],
  },
  {
    label: "分析・依頼",
    to: "/ask",
    hint: "依頼・ログ・版数",
    items: [
      { to: "/ask", label: "ことばで依頼" },
      { to: "/pipeline", label: "処理の流れ" },
      { to: "/syslog", label: "ログ解析" },
      { to: "/calc", label: "試算" },
      { to: "/os", label: "版数と状態" },
    ],
  },
  {
    label: "資料",
    to: "/import",
    hint: "取り込み・製品情報",
    items: [
      { to: "/import", label: "台帳の取り込み" },
      { to: "/reference", label: "製品情報" },
      { to: "/documents", label: "メーカー資料" },
    ],
  },
  {
    label: "手引き",
    to: "/glossary",
    hint: "用語と対応機能",
    items: [
      { to: "/glossary", label: "用語集" },
      { to: "/capabilities", label: "対応機能" },
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
