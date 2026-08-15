import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { ReplacementTarget } from "../api/types";
import { formatDate } from "../lib/format";

const YEN = new Intl.NumberFormat("ja-JP", {
  style: "currency",
  currency: "JPY",
  maximumFractionDigits: 0,
});

const SEVERITY_LABEL: Record<string, string> = {
  expired: "期限切れ",
  critical: "90日以内",
  warning: "1年以内",
  ok: "余裕あり",
  unknown: "情報未登録",
};

function TargetTable({
  targets,
  onPick,
}: {
  targets: ReplacementTarget[];
  onPick: (id: number) => void;
}) {
  if (targets.length === 0) return <div className="empty">対象はありません</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>機器</th>
          <th>拠点</th>
          <th>期限</th>
          <th>理由</th>
          <th>概算</th>
          <th>影響</th>
        </tr>
      </thead>
      <tbody>
        {targets.map((t) => (
          <tr key={t.device_id}>
            <td>
              <Link to={`/devices/${t.device_id}`}>{t.hostname}</Link>
              <div className="muted">
                {t.vendor} {t.model}
              </div>
            </td>
            <td className="muted">{t.site_name ?? "—"}</td>
            <td>
              {formatDate(t.due_on)}
              <div className="muted">
                <span className={`badge ${t.severity}`}>
                  {SEVERITY_LABEL[t.severity] ?? t.severity}
                </span>
              </div>
            </td>
            <td className="muted">{t.reason}</td>
            <td>{t.estimated_cost ? YEN.format(t.estimated_cost) : "—"}</td>
            <td>
              {t.impact_count > 0 ? (
                <button onClick={() => onPick(t.device_id)}>{t.impact_count}台に影響</button>
              ) : (
                <button onClick={() => onPick(t.device_id)} className="">
                  確認
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ReplacementPage() {
  const [years, setYears] = useState(5);
  const [impactFor, setImpactFor] = useState<number | null>(null);

  const { data: plan } = useQuery({
    queryKey: ["replacementPlan", years],
    queryFn: () => api.replacementPlan(years),
  });
  const { data: impact } = useQuery({
    queryKey: ["replacementImpact", impactFor],
    queryFn: () => api.replacementImpact(impactFor!),
    enabled: impactFor !== null,
  });

  const peak = Math.max(1, ...(plan?.years ?? []).map((y) => y.device_count));

  return (
    <>
      <h1>更改支援</h1>
      <p className="subtitle">
        保守期限と EoL の<strong>早い方</strong>を実質の期限として、年度別に並べます。
        年度は4月始まりです。
        <br />
        金額は年間保守費から逆算した<strong>概算</strong>なので、稟議には見積で置き換えてください。
      </p>

      <div className="toolbar">
        <label htmlFor="years">対象期間</label>
        <select id="years" value={years} onChange={(e) => setYears(Number(e.target.value))}>
          {[3, 5, 7, 10].map((n) => (
            <option key={n} value={n}>
              {n}年分
            </option>
          ))}
        </select>
      </div>

      {plan && (
        <>
          <div className="cards">
            <div className="card">
              <div className="label">対象機器</div>
              <div className="value">{plan.total_devices}台</div>
            </div>
            <div className="card">
              <div className="label">概算合計</div>
              <div className="value">{YEN.format(plan.total_cost)}</div>
            </div>
            <div className="card">
              <div className="label">期限が未登録</div>
              <div className="value">{plan.unscheduled.length}台</div>
            </div>
          </div>

          <h2>年度別の台数</h2>
          <table>
            <thead>
              <tr>
                <th style={{ width: 110 }}>年度</th>
                <th>台数</th>
                <th style={{ width: 160 }}>概算</th>
              </tr>
            </thead>
            <tbody>
              {plan.years.map((year) => (
                <tr key={year.fiscal_year}>
                  <td>
                    <strong>{year.label}</strong>
                  </td>
                  <td>
                    <div
                      style={{
                        display: "inline-block",
                        height: 14,
                        borderRadius: 3,
                        background: "var(--accent)",
                        width: `${(year.device_count / peak) * 70}%`,
                        minWidth: year.device_count ? 8 : 0,
                        verticalAlign: "middle",
                        marginRight: 8,
                      }}
                    />
                    {year.device_count}台
                  </td>
                  <td>{year.estimated_cost ? YEN.format(year.estimated_cost) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {plan.years
            .filter((y) => y.device_count > 0)
            .map((year) => (
              <div key={year.fiscal_year}>
                <h2>
                  {year.label}（{year.device_count}台 / 概算 {YEN.format(year.estimated_cost)}）
                </h2>
                <TargetTable targets={year.targets} onPick={setImpactFor} />
              </div>
            ))}

          {plan.unscheduled.length > 0 && (
            <>
              <h2>期限が分からない機器</h2>
              <p className="muted">
                保守期限も EoL も登録されていないため、年度に割り付けられません。
                リファレンス管理で型番の EoL を登録すると自動で入ります。
              </p>
              <TargetTable targets={plan.unscheduled} onPick={setImpactFor} />
            </>
          )}
        </>
      )}

      {impact && (
        <>
          <h2>{impact.hostname} を止めたときの影響</h2>
          <div
            className="card"
            style={{
              borderColor: impact.isolated.length ? "var(--sev-critical)" : "var(--sev-ok)",
            }}
          >
            {impact.note}
          </div>
          <div className="form-grid" style={{ marginTop: 12 }}>
            <div className="field">
              <label>直接つながっている機器</label>
              <div>{impact.directly_connected.join(" / ") || "—"}</div>
            </div>
            <div className="field">
              <label>経路が失われる機器</label>
              <div>{impact.isolated.join(" / ") || "—"}</div>
            </div>
          </div>
          <div className="actions">
            <Link className="button" to="/topology">
              トポロジー図で見る
            </Link>
            <button onClick={() => setImpactFor(null)}>閉じる</button>
          </div>
        </>
      )}
    </>
  );
}
