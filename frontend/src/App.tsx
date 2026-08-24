import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "./api/client";
import Logo from "./components/Logo";
import { CollapseIcon, GearIcon, MenuIcon, PinIcon } from "./components/icons";
import { WINDOW_FLAG } from "./components/WindowLink";
import { SECTIONS, isCurrent, sectionFor } from "./nav";

const OPEN_KEY = "netvision.sidebar";
const PIN_KEY = "netvision.sidebar.pinned";

/** タブレットくらいの幅では、メニューを出しっぱなしにすると図が潰れる。 */
const NARROW = "(max-width: 900px)";

function isNarrow(): boolean {
  return window.matchMedia(NARROW).matches;
}

/**
 * 別ウィンドウで開かれた画面か。
 *
 * 1台を見るためだけの窓にメニューを出しても押す相手がいないし、幅も取る。
 * 印は開くときに URL へ付ける（WindowLink）。
 */
function isOwnWindow(search: string): boolean {
  return new URLSearchParams(search).get(WINDOW_FLAG) === "1";
}

export default function App() {
  // プレビュー環境かどうか。データが消えることを黙っていると事故のもとになる
  const { data: meta } = useQuery({
    queryKey: ["meta"],
    queryFn: () => api.meta(),
  });
  const location = useLocation();
  const bare = isOwnWindow(location.search);

  // 常時表示。画面が狭くても閉じずに出したままにする
  const [pinned, setPinned] = useState(
    () => localStorage.getItem(PIN_KEY) === "true",
  );
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(OPEN_KEY);
    if (saved === "open") return true;
    if (saved === "closed") return false;
    return !isNarrow();
  });
  useEffect(
    () => localStorage.setItem(OPEN_KEY, open ? "open" : "closed"),
    [open],
  );
  useEffect(() => localStorage.setItem(PIN_KEY, String(pinned)), [pinned]);

  // 狭い画面ではメニューが図に覆いかぶさるので、選んだら閉じる。
  // 常時表示にしている場合と、広い画面では閉じない
  useEffect(() => {
    if (!pinned && isNarrow()) setOpen(false);
  }, [location.pathname, pinned]);

  // 常時表示に切り替えたら、その場で開く
  useEffect(() => {
    if (pinned) setOpen(true);
  }, [pinned]);

  // いま開いている画面が属するまとまり。中の画面はページの上にタブで出す
  const section = sectionFor(location.pathname);

  if (bare) {
    // 印刷プレビューのような窓。中身だけを出す
    return (
      <div className="layout bare">
        <main className="main">
          <Outlet />
        </main>
      </div>
    );
  }

  return (
    <div
      className={`layout ${open ? "" : "collapsed"} ${pinned ? "pinned" : ""}`}
    >
      <aside className="sidebar">
        {/* 設定はロゴの隣に置く。下に置くと、台帳のような長い画面では
            一番下まで送らないと届かない */}
        <div className="brand">
          <Link to="/dashboard" className="brand-home" aria-label="NetVision">
            <Logo />
          </Link>
          <div className="brand-tools">
            {/* 画鋲は狭い画面だけ。広い画面ではメニューが元から居座るので、
                押しても何も変わらないボタンになる */}
            <button
              className={`icon-button pin-button ${pinned ? "active" : ""}`}
              onClick={() => setPinned((value) => !value)}
              aria-pressed={pinned}
              aria-label="メニューを出したままにする"
              title={
                pinned
                  ? "メニューを出したままにしています"
                  : "画面が狭くてもメニューを出したままにする"
              }
            >
              <PinIcon />
            </button>
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `icon-button ${isActive ? "active" : ""}`
              }
              title="設定"
              aria-label="設定"
            >
              <GearIcon />
            </NavLink>
            <button
              className="icon-button"
              onClick={() => {
                // 常時表示のまま隠すと、次に開くときに迷う。隠すなら常時表示も解く
                if (pinned) setPinned(false);
                setOpen(false);
              }}
              aria-label="メニューを隠す"
              title="メニューを隠す"
            >
              <CollapseIcon />
            </button>
          </div>
        </div>
        <nav className="nav">
          {SECTIONS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={() =>
                section?.label === item.label ? "nav-section active" : "nav-section"
              }
            >
              {item.label}
              {item.hint && <span className="nav-hint">{item.hint}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* 狭い画面でメニューを開いたとき、外側を触れば閉じられるようにする */}
      {open && !pinned && (
        <div className="sidebar-scrim" onClick={() => setOpen(false)} />
      )}

      <main className="main">
        {/* メニューを隠しているときだけ、出すためのボタンと名前を置く。
            出ているときは、畳むボタンはメニューの中にある。
            空の帯を常に置くと、図に使える高さがその分減る */}
        {!open && (
          <div className="sidebar-controls">
            <button
              className="icon-button"
              onClick={() => setOpen(true)}
              aria-expanded={false}
              aria-label="メニューを出す"
              title="メニューを出す"
            >
              <MenuIcon />
            </button>
            {/* どの道具を見ているのかが分からなくなるのを防ぐ */}
            <Link to="/dashboard" className="topbar-brand" aria-label="NetVision">
              <Logo size={20} />
            </Link>
          </div>
        )}

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
        {/* まとまりの中の画面は、左ではなくここに出す。左を短く保ったまま、
            隣に何があるのかも見える */}
        {section && section.items.length > 1 && (
          <nav className="section-tabs" aria-label={section.label}>
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={() =>
                  isCurrent(location.pathname, item.to) ? "active" : ""
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        )}

        <Outlet />
      </main>
    </div>
  );
}
