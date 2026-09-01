import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { InsightCategory } from "../api/types";
import LinkCheckPanel from "../components/LinkCheckPanel";
import VlanCheckPanel from "../components/VlanCheckPanel";
import QueryError from "../components/QueryError";
import SeverityBadge from "../components/SeverityBadge";
import { formatCurrency } from "../lib/format";

const CATEGORY_LABEL: Record<InsightCategory, string> = {
  cost: "無駄をなくす",
  risk: "リスクを減らす",
  data: "台帳を整える",
  consolidation: "契約をまとめる",
};

const CATEGORY_HINT: Record<InsightCategory, string> = {
  cost: "払わなくてよい保守費が出ている可能性のある機器です。",
  risk: "サポート切れや保守なしで動いている機器です。",
  data: "情報が足りず、期限やリスクを判定できない機器です。",
  consolidation: "契約をまとめると単価や管理で有利になる可能性があります。",
};

export default function InsightsPage() {
  const [category, setCategory] = useState<InsightCategory | "">("");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["insights", category],
    queryFn: () => api.listInsights(category ? { category } : {}),
  });

  if (error) return <QueryError error={error} onRetry={() => refetch()} />;

  return (
    <>
      <h1>改善の指摘</h1>
      <p className="subtitle">
        登録済みの台帳から、払わなくてよい保守費と、抱えているリスクを洗い出します。
        削減できる年額の見込みも出すので、契約見直しや更改の根拠に使えます。
      </p>

      {isLoading || !data ? (
        <p className="muted">読み込み中…</p>
      ) : (
        <>
          <div className="cards">
            <div className="card">
              <div className="label">指摘の総数</div>
              <div className="value">{data.total}</div>
            </div>
            <div className="card">
              <div className="label">年間の削減見込み</div>
              <div className="value" style={{ color: "var(--sev-ok)" }}>
                {formatCurrency(data.annual_saving_total)}
              </div>
            </div>
            {(Object.keys(CATEGORY_LABEL) as InsightCategory[]).map((key) => (
              <div
                key={key}
                className="card clickable"
                onClick={() => setCategory(category === key ? "" : key)}
                style={{
                  borderColor: category === key ? "var(--accent)" : undefined,
                }}
              >
                <div className="label">{CATEGORY_LABEL[key]}</div>
                <div className="value">{data.by_category[key] ?? 0}</div>
              </div>
            ))}
          </div>

          {category && (
            <p className="muted" style={{ marginTop: 16 }}>
              {CATEGORY_HINT[category]}{" "}
              <button onClick={() => setCategory("")}>絞り込みを解除</button>
            </p>
          )}

          <h2>指摘の一覧</h2>
          {data.insights.length === 0 ? (
            <div className="empty">指摘はありません</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>重要度</th>
                  <th>対象</th>
                  <th>指摘</th>
                  <th>内容</th>
                  <th>年間削減見込み</th>
                  <th>推奨対応</th>
                </tr>
              </thead>
              <tbody>
                {data.insights.map((insight, index) => (
                  <tr key={`${insight.kind}-${insight.device_id}-${index}`}>
                    <td>
                      <SeverityBadge severity={insight.severity} />
                    </td>
                    <td>
                      {insight.device_id ? (
                        <Link to={`/devices/${insight.device_id}`}>
                          {insight.device_hostname}
                        </Link>
                      ) : (
                        <span className="muted">全体</span>
                      )}
                    </td>
                    <td>{insight.title}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 320 }}>
                      {insight.detail}
                    </td>
                    <td
                      style={{
                        color: insight.annual_saving
                          ? "var(--sev-ok)"
                          : undefined,
                      }}
                    >
                      {insight.annual_saving
                        ? formatCurrency(insight.annual_saving)
                        : "—"}
                    </td>
                    <td
                      className="muted"
                      style={{ whiteSpace: "normal", maxWidth: 320 }}
                    >
                      {insight.recommendation}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      <LinkCheckPanel />
      <VlanCheckPanel />
    </>
  );
}
