import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "./api/client";

/** メニューは「何をしたいか」で束ねる。項目が増えても探す範囲が変わらない。 */
const NAV_GROUPS: { label: string; items: { to: string; label: string }[] }[] = [
  {
    label: "見る",
    items: [
      { to: "/dashboard", label: "ダッシュボード" },
      { to: "/topology", label: "トポロジ図" },
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

const STORAGE_KEY = "netvision.sidebar";

/** タブレットくらいの幅では、メニューを出しっぱなしにすると図が潰れる。 */
const NARROW = "(max-width: 900px)";

export default function App() {
  // プレビュー環境かどうか。データが消えることを黙っていると事故のもとになる
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api.meta() });
  const location = useLocation();

  // 画面の広さで初期値を決める。狭い端末では畳んだ状態から始める
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "open") return true;
    if (saved === "closed") return false;
    return !window.matchMedia(NARROW).matches;
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, open ? "open" : "closed");
  }, [open]);

  // 狭い画面ではメニューが図に覆いかぶさるので、選んだら閉じる。
  // 広い画面では出したままの方が使いやすいので閉じない
  useEffect(() => {
    if (window.matchMedia(NARROW).matches) setOpen(false);
  }, [location.pathname]);

  return (
    <div className={`layout ${open ? "" : "collapsed"}`}>
      <aside className="sidebar">
        <div className="brand">
          Net<span>Vision</span>
        </div>
        <nav className="nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="nav-group">
              <div className="nav-group-label">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => (isActive ? "active" : "")}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      {/* 狭い画面でメニューを開いたとき、外側を触れば閉じられるようにする */}
      {open && <div className="sidebar-scrim" onClick={() => setOpen(false)} />}

      <main className="main">
        <button
          className="sidebar-toggle"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          title={open ? "メニューを隠す" : "メニューを出す"}
        >
          ☰<span className="sidebar-toggle-text"> メニュー</span>
        </button>

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
