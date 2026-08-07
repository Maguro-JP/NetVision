import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { formatDate } from "../lib/format";

function severityColor(score: number | null): string | undefined {
  if (score === null) return undefined;
  if (score >= 9.0) return "var(--sev-expired)";
  if (score >= 7.0) return "var(--sev-critical)";
  if (score >= 4.0) return "var(--sev-warning)";
  return "var(--muted)";
}

export default function VulnerabilitiesPage() {
  const [view, setView] = useState<"findings" | "catalog">("findings");
  const [affectingOnly, setAffectingOnly] = useState(true);

  const { data: summary, isLoading } = useQuery({
    queryKey: ["vulnerabilityMatches"],
    queryFn: () => api.vulnerabilityMatches(),
  });
  const { data: catalog } = useQuery({
    queryKey: ["vulnerabilities", affectingOnly],
    queryFn: () =>
      api.listVulnerabilities(affectingOnly ? { affecting_only: "true" } : {}),
    enabled: view === "catalog",
  });

  return (
    <>
      <h1>脆弱性</h1>
      <p className="subtitle">
        取り込んだ脆弱性情報と、機器のベンダー・型番・OSバージョンを突き合わせた結果です。
        データは <Link to="/reference">リファレンス管理</Link> から取り込みます。
      </p>

      {isLoading || !summary ? (
        <p className="muted">読み込み中…</p>
      ) : (
        <>
          <div className="cards">
            <div className="card">
              <div className="label">登録CVE</div>
              <div className="value">{summary.total_vulnerabilities}</div>
            </div>
            <div className="card">
              <div className="label">影響を受ける機器</div>
              <div
                className="value"
                style={{ color: summary.devices_affected ? "var(--sev-expired)" : undefined }}
              >
                {summary.devices_affected}
              </div>
            </div>
            <div className="card">
              <div className="label">悪用確認済み</div>
              <div
                className="value"
                style={{
                  color: summary.known_exploited_findings ? "var(--sev-expired)" : undefined,
                }}
              >
                {summary.known_exploited_findings}
              </div>
            </div>
            <div className="card">
              <div className="label">CVSS 9.0以上</div>
              <div className="value">{summary.critical_findings}</div>
            </div>
            <div className="card">
              <div className="label">判定不能（版数未登録）</div>
              <div className="value" style={{ color: "var(--sev-unknown)" }}>
                {summary.devices_without_version}
              </div>
            </div>
          </div>

          {summary.devices_without_version > 0 && (
            <p className="muted" style={{ marginTop: 12 }}>
              OSバージョンが未登録の機器が {summary.devices_without_version} 台あります。
              判定できないので、<Link to="/configs">Config を取り込む</Link>
              と自動で埋まります。
            </p>
          )}

          <div className="toolbar" style={{ marginTop: 16 }}>
            <button
              className={view === "findings" ? "primary" : ""}
              onClick={() => setView("findings")}
            >
              機器ごとの該当
            </button>
            <button
              className={view === "catalog" ? "primary" : ""}
              onClick={() => setView("catalog")}
            >
              CVE一覧
            </button>
            {view === "catalog" && (
              <label className="muted" style={{ alignSelf: "center" }}>
                <input
                  type="checkbox"
                  checked={affectingOnly}
                  onChange={(e) => setAffectingOnly(e.target.checked)}
                  style={{ marginRight: 6 }}
                />
                自社機器に該当するものだけ
              </label>
            )}
          </div>

          {view === "findings" ? (
            summary.findings.length === 0 ? (
              <div className="empty">該当する脆弱性はありません</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>機器</th>
                    <th>CVE</th>
                    <th>CVSS</th>
                    <th>状況</th>
                    <th>判定根拠</th>
                    <th>修正版</th>
                    <th>情報源</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.findings.map((finding) => (
                    <tr key={`${finding.device_id}-${finding.cve_id}`}>
                      <td>
                        <Link to={`/devices/${finding.device_id}`}>{finding.hostname}</Link>
                      </td>
                      <td>{finding.cve_id}</td>
                      <td style={{ color: severityColor(finding.cvss_score), fontWeight: 600 }}>
                        {finding.cvss_score ?? "—"}
                      </td>
                      <td>
                        {finding.known_exploited ? (
                          <span className="badge expired">悪用確認済み</span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="muted" style={{ whiteSpace: "normal", maxWidth: 280 }}>
                        {finding.matched_on}
                      </td>
                      <td>{finding.fixed_version ?? "—"}</td>
                      <td className="muted">
                        {finding.source}
                        {finding.fetched_at && ` / ${formatDate(finding.fetched_at)}取得`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          ) : (catalog ?? []).length === 0 ? (
            <div className="empty">該当する CVE はありません</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>CVE</th>
                  <th>CVSS</th>
                  <th>状況</th>
                  <th>概要</th>
                  <th>公開日</th>
                  <th>該当機器</th>
                  <th>情報源</th>
                </tr>
              </thead>
              <tbody>
                {(catalog ?? []).map((cve) => (
                  <tr key={cve.id}>
                    <td>
                      {cve.source_url ? (
                        <a href={cve.source_url} target="_blank" rel="noreferrer">
                          {cve.cve_id}
                        </a>
                      ) : (
                        cve.cve_id
                      )}
                    </td>
                    <td style={{ color: severityColor(cve.cvss_score), fontWeight: 600 }}>
                      {cve.cvss_score ?? "—"}
                    </td>
                    <td>
                      {cve.known_exploited && <span className="badge expired">悪用確認済み</span>}
                    </td>
                    <td style={{ whiteSpace: "normal", maxWidth: 420 }}>{cve.title ?? "—"}</td>
                    <td className="muted">{formatDate(cve.published)}</td>
                    <td
                      style={{
                        color: cve.affected_device_count ? "var(--sev-expired)" : undefined,
                      }}
                    >
                      {cve.affected_device_count}
                    </td>
                    <td className="muted">
                      {cve.source}
                      {cve.fetched_at && ` / ${formatDate(cve.fetched_at)}取得`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
