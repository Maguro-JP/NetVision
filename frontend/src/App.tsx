import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "./api/client";

/** メニューは「何をしたいか」で束ねる。項目が増えても探す範囲が変わらない。 */
const NAV_GROUPS: { label: string; items: { to: string; label: string }[] }[] = [
  {
    label: "見る",
    items: [
      { to: "/dashboard", label: "ダッシュボード" },
      { to: "/topology", label: "トポロジー図" },
      // /space（立体で眺める）は隠し機能。メニューには出さず、URL 直打ちだけ。
      { to: "/insights", label: "最適化の指摘" },
    ],
  },
  {
    label: "台帳",
    items: [
      { to: "/devices", label: "機器一覧" },
      { to: "/sites", label: "拠点" },
      { to: "/vlans", label: "VLAN 台帳" },
    ],
  },
  {
    label: "セキュリティ",
    items: [
      { to: "/exposures", label: "対処状況" },
      { to: "/vulnerabilities", label: "脆弱性" },
      { to: "/remediation", label: "脆弱性への対処" },
    ],
  },
  {
    label: "計画・変更",
    items: [
      { to: "/replacement", label: "更改支援" },
      { to: "/scenarios", label: "将来構成シナリオ" },
      { to: "/config-plans", label: "Config計画（差分生成）" },
      { to: "/config-templates", label: "Configテンプレート" },
      { to: "/changes", label: "変更履歴" },
    ],
  },
  {
    label: "取り込み・資料",
    items: [
      { to: "/configs", label: "Configの取り込み" },
      { to: "/import", label: "台帳の取り込み" },
      { to: "/reference", label: "リファレンス管理" },
      { to: "/documents", label: "ベンダー資料" },
    ],
  },
];

const OPEN_KEY = "netvision.sidebar";
const PIN_KEY = "netvision.sidebar.pinned";
const FOLDED_KEY = "netvision.sidebar.folded";

/** タブレットくらいの幅では、メニューを出しっぱなしにすると図が潰れる。 */
const NARROW = "(max-width: 900px)";

function isNarrow(): boolean {
  return window.matchMedia(NARROW).matches;
}

function readList(key: string): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

export default function App() {
  // プレビュー環境かどうか。データが消えることを黙っていると事故のもとになる
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api.meta() });
  const location = useLocation();

  // 常時表示。画面が狭くても閉じずに出したままにする
  const [pinned, setPinned] = useState(() => localStorage.getItem(PIN_KEY) === "true");
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(OPEN_KEY);
    if (saved === "open") return true;
    if (saved === "closed") return false;
    return !isNarrow();
  });
  // 畳んだグループ。項目が何処にあるか分からなくなるのが一番困るので、
  // 既定は全部開いた状態にしておく
  const [folded, setFolded] = useState<string[]>(() => readList(FOLDED_KEY));

  useEffect(() => localStorage.setItem(OPEN_KEY, open ? "open" : "closed"), [open]);
  useEffect(() => localStorage.setItem(PIN_KEY, String(pinned)), [pinned]);
  useEffect(() => localStorage.setItem(FOLDED_KEY, JSON.stringify(folded)), [folded]);

  // 狭い画面ではメニューが図に覆いかぶさるので、選んだら閉じる。
  // 常時表示にしている場合と、広い画面では閉じない
  useEffect(() => {
    if (!pinned && isNarrow()) setOpen(false);
  }, [location.pathname, pinned]);

  // 常時表示に切り替えたら、その場で開く
  useEffect(() => {
    if (pinned) setOpen(true);
  }, [pinned]);

  const toggleGroup = (label: string) =>
    setFolded((current) =>
      current.includes(label) ? current.filter((item) => item !== label) : [...current, label],
    );

  return (
    <div className={`layout ${open ? "" : "collapsed"} ${pinned ? "pinned" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          Net<span>Vision</span>
        </div>
        <nav className="nav">
          {NAV_GROUPS.map((group) => {
            // 今いる画面が入っているグループは畳まない。開いて探す手間を無くす
            const current = group.items.some((item) => item.to === location.pathname);
            const collapsed = folded.includes(group.label) && !current;

            return (
              <div key={group.label} className={`nav-group ${collapsed ? "folded" : ""}`}>
                <button
                  className="nav-group-label"
                  onClick={() => toggleGroup(group.label)}
                  aria-expanded={!collapsed}
                  title={collapsed ? "開く" : "畳む"}
                >
                  <span className="nav-caret">{collapsed ? "▸" : "▾"}</span>
                  {group.label}
                  {collapsed && <span className="nav-count">{group.items.length}</span>}
                </button>
                {!collapsed &&
                  group.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={({ isActive }) => (isActive ? "active" : "")}
                    >
                      {item.label}
                    </NavLink>
                  ))}
              </div>
            );
          })}
        </nav>
      </aside>

      {/* 狭い画面でメニューを開いたとき、外側を触れば閉じられるようにする */}
      {open && !pinned && <div className="sidebar-scrim" onClick={() => setOpen(false)} />}

      <main className="main">
        {/* 開閉ボタンと常時表示の切り替えは、メニューの外に置く。
            メニューの中に入れると、閉じたときに触れなくなって戻せない */}
        <div className="sidebar-controls">
          <button
            className="sidebar-toggle"
            onClick={() => {
              // 常時表示のまま隠すと、次に開くときに迷う。隠すなら常時表示も解く
              if (pinned) setPinned(false);
              setOpen((value) => !value);
            }}
            aria-expanded={open}
            title={open ? "メニューを隠す" : "メニューを出す"}
          >
            ☰<span className="sidebar-toggle-text"> メニュー</span>
          </button>
          <label className="sidebar-pin" title="画面が狭くてもメニューを出したままにします">
            <input
              type="checkbox"
              checked={pinned}
              onChange={(event) => setPinned(event.target.checked)}
            />{" "}
            常時表示
          </label>
        </div>

        {meta?.schema_phase === "needs_confirm" && (
          <div className="demo-banner">
            <strong>表を更新しました。</strong>
            機器の台数や図を確かめたら、<Link to="/migration">控えの確認</Link>
            へ進んでください。控えは確認するまで残しています。
          </div>
        )}
        {meta?.demo_mode && (
          <div className="demo-banner">
            <strong>デモ環境です。</strong>
            データは永続しません（入力してもしばらくすると消えます）。
            画面と操作感の確認用で、実機の情報は入れないでください。
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
