import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { ExposureRow } from "../api/types";
import { formatDate } from "../lib/format";

const STATUS_LABEL: Record<string, string> = {
  open: "未対応",
  in_progress: "対応中",
  mitigated: "暫定対処済み",
  fixed: "恒久対処済み",
  accepted: "リスク受容",
  not_affected: "影響なし",
  resolved: "該当しなくなった",
};

// 選べるのは人が決める状態だけ。resolved は自動でしか付かない
const DECIDABLE = ["open", "in_progress", "mitigated", "fixed", "accepted", "not_affected"];

function FixPanel({ row, onClose }: { row: ExposureRow; onClose: () => void }) {
  const [tab, setTab] = useState<"diff" | "fixed" | "current" | "rollback">("diff");
  const { data: fix, error } = useQuery({
    queryKey: ["fixConfig", row.device_id, row.remediation_id],
    queryFn: () => api.fixConfig(row.device_id, row.remediation_id!),
    enabled: row.remediation_id !== null,
  });

  if (row.remediation_id === null) {
    return (
      <div className="card" style={{ marginTop: 12, borderColor: "var(--sev-warning)" }}>
        対応版 Config を出すには、適用する対処を先に指定してください。
        <div className="actions">
          <Link className="button" to="/remediation">
            対処を選ぶ
          </Link>
          <button onClick={onClose}>閉じる</button>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ marginTop: 12, borderColor: "var(--sev-expired)" }}>
        {(error as Error).message}
        <div className="actions">
          <button onClick={onClose}>閉じる</button>
        </div>
      </div>
    );
  }

  if (!fix) return null;

  const body =
    tab === "current"
      ? fix.current_full
      : tab === "fixed"
        ? fix.fixed_full
        : tab === "rollback"
          ? fix.diff.rollback_script
          : fix.diff.script;

  return (
    <>
      <h3>
        {fix.hostname} / {fix.cve_id} の対応版 Config
      </h3>
      <p className="muted">{fix.remediation_summary}</p>

      {fix.notes.map((note) => (
        <div key={note} className="card" style={{ marginTop: 8, borderColor: "var(--sev-warning)" }}>
          {note}
        </div>
      ))}

      {fix.already_applied ? (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--sev-ok)" }}>
          ✓ この対処の設定は現行 config に既に入っています（投入するものはありません）
        </div>
      ) : fix.diff.verified ? (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--sev-ok)" }}>
          ✓ この投入コマンドを当てると対応版 config と同じ状態になることを確認しました
        </div>
      ) : (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--sev-expired)" }}>
          <strong>収束しないためコマンドを出していません。</strong>
          <ul>
            {fix.diff.mismatches.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      {fix.diff.risks.length > 0 && (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--sev-critical)" }}>
          <strong>通信・接続に影響します</strong>
          <ul>
            {fix.diff.risks.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="toolbar" style={{ marginTop: 12 }}>
        {(
          [
            ["diff", "投入コマンド"],
            ["fixed", "対応版フル"],
            ["current", "現行フル"],
            ["rollback", "切り戻し"],
          ] as const
        ).map(([key, label]) => (
          <button key={key} className={tab === key ? "primary" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      <pre className="diff">{body || "（空です）"}</pre>
      <div className="actions">
        <a className="button" href={api.fixConfigDownloadUrl(fix.device_id, fix.remediation_id, tab)}>
          ファイルで落とす
        </a>
        <button onClick={() => navigator.clipboard?.writeText(body)}>コピー</button>
        <button onClick={onClose}>閉じる</button>
      </div>
      <p className="muted">
        内容を確認してから、手で機器に投入してください（このツールから投入はしません）。
      </p>
    </>
  );
}

export default function ExposuresPage() {
  const queryClient = useQueryClient();
  const [unhandledOnly, setUnhandledOnly] = useState(true);
  const [fixFor, setFixFor] = useState<ExposureRow | null>(null);
  const [syncNote, setSyncNote] = useState<string[]>([]);

  const { data: rows } = useQuery({
    queryKey: ["exposures", unhandledOnly],
    queryFn: () => api.listExposures({ unhandled_only: unhandledOnly }),
  });
  const { data: summary } = useQuery({
    queryKey: ["exposureSummary"],
    queryFn: () => api.exposureSummary(),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["exposures"] });
    queryClient.invalidateQueries({ queryKey: ["exposureSummary"] });
  };

  const sync = useMutation({
    mutationFn: () => api.syncExposures(),
    onSuccess: (result) => {
      setSyncNote(result.messages);
      refresh();
    },
  });

  const decide = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => {
      const note =
        status === "accepted"
          ? (prompt("リスクを受容する理由を入力してください") ?? "")
          : undefined;
      return api.decideExposure(id, { status, note });
    },
    onSuccess: refresh,
  });

  const verify = useMutation({
    mutationFn: (id: number) => api.verifyExposureConfig(id),
    onSuccess: refresh,
  });

  return (
    <>
      <h1>対処状況</h1>
      <p className="subtitle">
        機器 × CVE を1件ずつ台帳にして、取りこぼさないようにします。
        <br />
        <strong>「対処を書いた」と「その機器に入れた」は別</strong>なので分けて持ちます。
        回避策は当てても機器の版は変わらないため、<strong>該当は残ったまま</strong>です。
      </p>

      <div className="toolbar">
        <button className="primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
          該当状況を洗い直す
        </button>
        <label>
          <input
            type="checkbox"
            checked={unhandledOnly}
            onChange={(e) => setUnhandledOnly(e.target.checked)}
          />{" "}
          未対応・対応中だけ表示
        </label>
      </div>

      {syncNote.length > 0 && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          {syncNote.map((m) => (
            <div key={m}>{m}</div>
          ))}
        </div>
      )}

      {summary && (
        <div className="cards" style={{ marginTop: 12 }}>
          <div className="card">
            <div className="label">未対応・対応中</div>
            <div className="value">{summary.unhandled}</div>
          </div>
          <div className="card">
            <div className="label">悪用確認済みで未対応</div>
            <div className="value" style={{ color: "var(--sev-expired)" }}>
              {summary.known_exploited_unhandled}
            </div>
          </div>
          <div className="card">
            <div className="label">是正期限切れ</div>
            <div className="value">{summary.overdue}</div>
          </div>
          <div className="card">
            <div className="label">暫定対処のみ</div>
            <div className="value">{summary.mitigated_but_still_affected}</div>
          </div>
          <div className="card">
            <div className="label">済みだが未確認</div>
            <div className="value">{summary.unverified}</div>
          </div>
          <div className="card">
            <div className="label">差し戻し</div>
            <div className="value">{summary.reopened}</div>
          </div>
        </div>
      )}

      {(rows ?? []).length === 0 ? (
        <div className="empty">
          該当はありません。CVE や機器を登録した後は「該当状況を洗い直す」を実行してください。
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>機器</th>
              <th>CVE</th>
              <th>状態</th>
              <th>裏取り</th>
              <th>期限</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((row) => (
              <tr key={row.id}>
                <td>
                  <Link to={`/devices/${row.device_id}`}>{row.hostname}</Link>
                  <div className="muted">{row.os_version ?? "版数不明"}</div>
                </td>
                <td style={{ whiteSpace: "normal", maxWidth: 300 }}>
                  <strong>{row.cve_id}</strong>{" "}
                  {row.known_exploited && <span className="badge expired">悪用確認済み</span>}
                  {row.cvss_score !== null && (
                    <span className="muted"> CVSS {row.cvss_score}</span>
                  )}
                  {row.title && <div className="muted">{row.title}</div>}
                  {row.reopen_reason && (
                    <div className="muted" style={{ color: "var(--sev-warning)" }}>
                      差し戻し: {row.reopen_reason}
                    </div>
                  )}
                </td>
                <td>
                  <select
                    value={row.status}
                    onChange={(e) => decide.mutate({ id: row.id, status: e.target.value })}
                  >
                    {DECIDABLE.map((value) => (
                      <option key={value} value={value}>
                        {STATUS_LABEL[value]}
                      </option>
                    ))}
                    {row.status === "resolved" && <option value="resolved">該当しなくなった</option>}
                  </select>
                  {row.decided_by && <div className="muted">{row.decided_by}</div>}
                </td>
                <td>
                  {row.config_verified === null ? (
                    <span className="muted">—</span>
                  ) : row.config_verified ? (
                    <span className="badge ok">設定を確認</span>
                  ) : (
                    <span className="badge critical">未反映</span>
                  )}
                  <div>
                    <button onClick={() => verify.mutate(row.id)} disabled={!row.remediation_id}>
                      確認
                    </button>
                  </div>
                </td>
                <td className={row.overdue ? "" : "muted"}>
                  {formatDate(row.due_on)}
                  {row.overdue && <div className="badge expired">期限超過</div>}
                </td>
                <td>
                  <button onClick={() => setFixFor(row)}>対応版Config</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {fixFor && <FixPanel row={fixFor} onClose={() => setFixFor(null)} />}
    </>
  );
}
