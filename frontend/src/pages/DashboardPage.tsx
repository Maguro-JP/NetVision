import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Severity } from "../api/types";
import DeviceTable from "../components/DeviceTable";
import { SEVERITY_LABEL } from "../lib/format";

const HIGHLIGHTED: Severity[] = ["expired", "critical", "warning"];

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["summary"],
    queryFn: api.summary,
  });

  if (isLoading) return <p className="muted">読み込み中…</p>;
  if (error) return <div className="error">{(error as Error).message}</div>;
  if (!data) return null;

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
      <DeviceTable devices={data.upcoming} />

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
