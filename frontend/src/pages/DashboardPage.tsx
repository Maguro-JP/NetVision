import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Severity } from "../api/types";
import DeviceTable from "../components/DeviceTable";
import QueryError from "../components/QueryError";
import { SEVERITY_LABEL } from "../lib/format";

const HIGHLIGHTED: Severity[] = ["expired", "critical", "warning"];

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["summary"],
    queryFn: api.summary,
  });

  if (isLoading) return <p className="muted">読み込み中…</p>;
  if (error) return <QueryError error={error} onRetry={() => refetch()} />;
  if (!data) return null;

  if (data.total_devices === 0) {
    return (
      <>
        <h1>ダッシュボード</h1>
        <p className="subtitle">まだ機器がありません。どれか1つから始められます。</p>
        <div className="empty empty-start">
          <p>Config か台帳を取り込むと、図も期限もそこから育ちます。</p>
          <div className="actions" style={{ justifyContent: "center" }}>
            <Link to="/configs">
              <button className="primary">Config を取り込む</button>
            </Link>
            <Link to="/import">
              <button>台帳（Excel / CSV）を取り込む</button>
            </Link>
            <Link to="/devices/new">
              <button>1台だけ手で登録</button>
            </Link>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>ダッシュボード</h1>
      <p className="subtitle">保守期限とサポート終了（EoL）の状況を一覧します。</p>

      <div className="cards">
        <div className="card">
          <div className="label">登録機器</div>
          <div className="value">{data.total_devices}</div>
        </div>
        <div className="card">
          <div className="label">稼働中</div>
          <div className="value">{data.active_devices}</div>
        </div>
        {HIGHLIGHTED.map((sev) => (
          <div
            key={sev}
            className="card clickable"
            onClick={() => navigate(`/devices?severity=${sev}`)}
          >
            <div className="label">{SEVERITY_LABEL[sev]}</div>
            <div className="value" style={{ color: `var(--sev-${sev})` }}>
              {data.by_overall_severity[sev]}
            </div>
          </div>
        ))}
        <div className="card clickable" onClick={() => navigate("/devices?severity=unknown")}>
          <div className="label">ライフサイクル情報未登録</div>
          <div className="value">{data.devices_without_lifecycle_data}</div>
        </div>
      </div>

      <h2>期限が近い機器</h2>
      {data.upcoming.length === 0 ? (
        <div className="empty">期限が近い機器はありません</div>
      ) : (
        <DeviceTable devices={data.upcoming} />
      )}

      <h2>ベンダー別の台数</h2>
      {data.by_vendor.length === 0 ? (
        <div className="empty">機器が登録されていません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ベンダー</th>
              <th>台数</th>
            </tr>
          </thead>
          <tbody>
            {data.by_vendor.map((v) => (
              <tr key={v.vendor}>
                <td>{v.vendor}</td>
                <td>{v.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
