import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/dashboard", label: "ダッシュボード" },
  { to: "/topology", label: "トポロジ図" },
  { to: "/devices", label: "機器一覧" },
  { to: "/sites", label: "拠点" },
  { to: "/vulnerabilities", label: "脆弱性" },
  { to: "/insights", label: "最適化の指摘" },
  { to: "/scenarios", label: "将来構成シナリオ" },
  { to: "/changes", label: "変更履歴" },
  { to: "/configs", label: "Configの取り込み" },
  { to: "/documents", label: "ベンダー資料" },
  { to: "/config-templates", label: "Configテンプレート" },
  { to: "/reference", label: "リファレンス管理" },
  { to: "/import", label: "台帳の取り込み" },
];

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Net<span>Vision</span>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
