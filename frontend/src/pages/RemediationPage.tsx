import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { RemediationFinding } from "../api/types";

const KIND_LABEL: Record<string, string> = {
  upgrade: "修正版へ上げる",
  workaround: "設定で回避する",
  mitigation: "影響を下げる",
  accepted: "リスクを受容",
  not_applicable: "構成上影響しない",
};

const EMPTY = {
  kind: "workaround",
  summary: "",
  vendor: "",
  model: "",
  os_name: "",
  applies_from: "",
  applies_to: "",
  fixed_version: "",
  procedure: "",
  verified_by: "",
  verified_on: "",
};

function Finding({
  finding,
  onWrite,
}: {
  finding: RemediationFinding;
  onWrite: (cve: string) => void;
}) {
  return (
    <div
      className="card"
      style={{
        marginTop: 12,
        borderColor: finding.known_exploited
          ? "var(--sev-expired)"
          : finding.has_remediation
            ? "var(--sev-ok)"
            : "var(--sev-warning)",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong>{finding.cve_id}</strong>
        {finding.known_exploited && <span className="badge expired">悪用確認済み</span>}
        {finding.cvss_score !== null && (
          <span className="muted">CVSS {finding.cvss_score}</span>
        )}
        {!finding.has_remediation && <span className="badge warning">対処が未登録</span>}
      </div>
      {finding.title && <div>{finding.title}</div>}
      <div className="muted">該当理由: {finding.matched_on}</div>

      {finding.fixed_version && (
        <div style={{ marginTop: 8 }}>
          修正版: <strong>{finding.fixed_version}</strong>
          {finding.upgrade_available ? "（上げれば直ります）" : ""}
        </div>
      )}

      {finding.remediations.length > 0 ? (
        <div style={{ marginTop: 10 }}>
          {finding.remediations.map((option) => (
            <div key={option.id} style={{ marginBottom: 10 }}>
              <div>
                <span className="badge ok">{KIND_LABEL[option.kind] ?? option.kind}</span>{" "}
                <strong>{option.summary}</strong>
              </div>
              {option.verified_on && (
                <div className="muted">
                  確認: {option.verified_by ?? "—"}（{option.verified_on}）
                </div>
              )}
              {option.procedure && <pre className="diff">{option.procedure}</pre>}
              <div className="muted">
                {option.source_document_title && <>根拠: {option.source_document_title}　</>}
                {option.config_template_name && (
                  <>
                    テンプレート:{" "}
                    <Link to="/config-templates">{option.config_template_name}</Link>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ marginTop: 10 }}>
          <p className="muted">
            公開データには回避コマンドが機械可読な形で入っていないため、ここは人が1回書きます。
            書いておくと同じ型番・同じバージョン帯の機器すべてで再利用されます。
          </p>
          {finding.suggested_documents.length > 0 && (
            <div className="muted">
              調べる手掛かり:{" "}
              {finding.suggested_documents.map((d) => d.title).join(" / ")}
            </div>
          )}
          <div className="actions">
            <button className="primary" onClick={() => onWrite(finding.cve_id)}>
              対処を登録する
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function RemediationPage() {
  const queryClient = useQueryClient();
  const [deviceId, setDeviceId] = useState("");
  const [form, setForm] = useState({ ...EMPTY, cve_id: "" });
  const [showForm, setShowForm] = useState(false);

  const { data: devices } = useQuery({
    queryKey: ["devices", {}],
    queryFn: () => api.listDevices(),
  });
  const { data: report } = useQuery({
    queryKey: ["deviceRemediation", deviceId],
    queryFn: () => api.deviceRemediation(Number(deviceId)),
    enabled: deviceId !== "",
  });

  const create = useMutation({
    mutationFn: () =>
      api.createRemediation(
        Object.fromEntries(Object.entries(form).filter(([, v]) => v !== "")),
      ),
    onSuccess: () => {
      setShowForm(false);
      setForm({ ...EMPTY, cve_id: "" });
      queryClient.invalidateQueries({ queryKey: ["deviceRemediation"] });
    },
  });

  const set = (key: string) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const startWriting = (cve: string) => {
    const device = (devices ?? []).find((d) => String(d.id) === deviceId);
    setForm({
      ...EMPTY,
      cve_id: cve,
      vendor: device?.vendor ?? "",
      model: device?.model ?? "",
      os_name: device?.os_name ?? "",
    });
    setShowForm(true);
  };

  return (
    <>
      <h1>脆弱性への対処</h1>
      <p className="subtitle">
        機器を選ぶと、該当する CVE と<strong>その機器で何をすればいいか</strong>が並びます。
        <br />
        該当の判定・修正版の推定・資料の提示までは自動です。回避策のコマンドは公開データに
        機械可読な形で存在しないため、<strong>一度だけ人が書きます</strong>。
        書いた対処はベンダー・型番・バージョン範囲で保存され、以後は自動で引き当てられます。
      </p>

      <div className="toolbar">
        <label htmlFor="device">対象の機器</label>
        <select id="device" value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
          <option value="">選択してください</option>
          {(devices ?? []).map((d) => (
            <option key={d.id} value={d.id}>
              {d.hostname}（{d.model} / {d.os_version ?? "版数不明"}）
            </option>
          ))}
        </select>
        {deviceId && (
          <button onClick={() => setShowForm((v) => !v)}>
            {showForm ? "入力をやめる" : "対処を手で追加"}
          </button>
        )}
      </div>

      {create.error && <div className="error">{(create.error as Error).message}</div>}

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="form-grid">
            <div className="field">
              <label htmlFor="cve">CVE番号 *</label>
              <input id="cve" required value={form.cve_id} onChange={set("cve_id")} />
            </div>
            <div className="field">
              <label htmlFor="kind">対処の種類</label>
              <select id="kind" value={form.kind} onChange={set("kind")}>
                {Object.entries(KIND_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="summary">対処の要約 *</label>
              <input
                id="summary"
                required
                placeholder="Web UI を無効化する"
                value={form.summary}
                onChange={set("summary")}
              />
            </div>
            <div className="field">
              <label htmlFor="vendor">ベンダー</label>
              <input id="vendor" value={form.vendor} onChange={set("vendor")} />
            </div>
            <div className="field">
              <label htmlFor="model">型番（`C9500*` で前方一致）</label>
              <input id="model" value={form.model} onChange={set("model")} />
            </div>
            <div className="field">
              <label htmlFor="from">適用バージョン（開始）</label>
              <input id="from" value={form.applies_from} onChange={set("applies_from")} />
            </div>
            <div className="field">
              <label htmlFor="to">適用バージョン（終了）</label>
              <input id="to" value={form.applies_to} onChange={set("applies_to")} />
            </div>
            <div className="field">
              <label htmlFor="fixed">修正版</label>
              <input id="fixed" value={form.fixed_version} onChange={set("fixed_version")} />
            </div>
            <div className="field">
              <label htmlFor="by">確認した人</label>
              <input id="by" value={form.verified_by} onChange={set("verified_by")} />
            </div>
            <div className="field">
              <label htmlFor="on">確認日</label>
              <input id="on" type="date" value={form.verified_on} onChange={set("verified_on")} />
            </div>
            <div className="field wide">
              <label htmlFor="procedure">手順（コマンドを含む文章でよい）</label>
              <textarea
                id="procedure"
                rows={8}
                value={form.procedure}
                onChange={set("procedure")}
                style={{ fontFamily: "monospace" }}
              />
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={create.isPending}>
              登録
            </button>
          </div>
        </form>
      )}

      {report && (
        <>
          <h2>
            {report.hostname}（{report.os_version ?? "版数不明"}）
          </h2>
          <div
            className="card"
            style={{
              borderColor: report.unresolved_count ? "var(--sev-warning)" : "var(--sev-ok)",
            }}
          >
            {report.note}
          </div>
          {report.findings.map((finding) => (
            <Finding key={finding.cve_id} finding={finding} onWrite={startWriting} />
          ))}
        </>
      )}
    </>
  );
}
