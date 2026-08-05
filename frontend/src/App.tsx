import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/dashboard", label: "ダッシュボード" },
  { to: "/devices", label: "機器一覧" },
  { to: "/sites", label: "拠点" },
  { to: "/insights", label: "最適化の指摘" },
  { to: "/scenarios", label: "将来構成シナリオ" },
  { to: "/changes", label: "変更履歴" },
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
