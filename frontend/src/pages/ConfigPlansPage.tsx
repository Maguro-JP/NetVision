import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { formatTimestamp } from "../lib/format";
import type { ConfigDraft, ConfigPlan, DiffCommand } from "../api/types";

const OUTPUT_TABS = [
  {
    key: "diff" as const,
    label: "差分",
    hint: "更改に合わせて既存機器に当てる投入コマンド",
  },
  {
    key: "future" as const,
    label: "将来フル",
    hint: "更改で入る新しい機器にそのまま投入する",
  },
  {
    key: "current" as const,
    label: "現行フル",
    hint: "機器故障のときの復旧に使う。取り込んだ running-config そのまま",
  },
  {
    key: "rollback" as const,
    label: "切り戻し",
    hint: "差分を当てた後、元に戻したいときに使う",
  },
];

const KIND_COLOR: Record<DiffCommand["kind"], string> = {
  add: "var(--sev-ok)",
  remove: "var(--sev-expired)",
  context: "var(--muted)",
};

export default function ConfigPlansPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [tab, setTab] = useState<(typeof OUTPUT_TABS)[number]["key"]>("diff");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    device_id: "",
    name: "",
    seed_from: "current",
  });
  // 資料を根拠にした下書き。依頼の文と、返ってきた結果
  const [wish, setWish] = useState("");
  const [suggestion, setSuggestion] = useState<ConfigDraft | null>(null);

  const { data: plans } = useQuery({
    queryKey: ["configPlans"],
    queryFn: () => api.listPlans(),
  });
  const { data: devices } = useQuery({
    queryKey: ["devices", {}],
    queryFn: () => api.listDevices(),
  });
  const { data: outputs } = useQuery({
    queryKey: ["planOutputs", selectedId],
    queryFn: () => api.planOutputs(selectedId!),
    enabled: selectedId !== null,
  });

  const selected = (plans ?? []).find((p) => p.id === selectedId) ?? null;

  useEffect(() => {
    if (selected && !editing) setDraft(selected.body);
  }, [selected, editing]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["configPlans"] });
    queryClient.invalidateQueries({ queryKey: ["planOutputs"] });
  };

  const create = useMutation({
    mutationFn: () =>
      api.createPlan({
        device_id: Number(form.device_id),
        name: form.name,
        seed_from: form.seed_from,
      }),
    onSuccess: (plan: ConfigPlan) => {
      setShowForm(false);
      setForm({ device_id: "", name: "", seed_from: "current" });
      setSelectedId(plan.id);
      refresh();
    },
  });

  const save = useMutation({
    mutationFn: () => api.updatePlan(selectedId!, { body: draft }),
    onSuccess: () => {
      setEditing(false);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deletePlan(id),
    onSuccess: () => {
      setSelectedId(null);
      refresh();
    },
  });

  const { data: llm } = useQuery({
    queryKey: ["llmStatus"],
    queryFn: () => api.llmStatus(),
  });

  // 手元にモデルを置けない職場向け。承認済みの AI へ貼る文と、その返事
  const [pasted, setPasted] = useState("");
  const [promptText, setPromptText] = useState("");

  const askForPrompt = useMutation({
    mutationFn: () =>
      api.draftPrompt(selected!.device_id, { request: wish, base: draft }),
    onSuccess: (result) => {
      setPromptText(result.body);
      setSuggestion(null);
    },
  });

  const check = useMutation({
    mutationFn: () =>
      api.reviewDraft(selected!.device_id, { request: wish, body: pasted }),
    onSuccess: (result) => setSuggestion(result),
  });

  // 下書きは、手元のモデルだと数十秒かかる。押した瞬間に受け付けて、
  // 進み具合を出しながら裏で作る（画面が固まったように見せない）
  const [jobId, setJobId] = useState<string | null>(null);

  const suggest = useMutation({
    mutationFn: () =>
      api.draftConfigInBackground(selected!.device_id, {
        request: wish,
        base: draft,
      }),
    onSuccess: (started) => {
      setSuggestion(null);
      setJobId(started.id);
    },
  });

  const { data: job } = useQuery({
    queryKey: ["draftJob", jobId],
    queryFn: () => api.job(jobId!),
    enabled: jobId !== null,
    // 終わるまで1秒ごとに様子を聞く
    refetchInterval: (query) =>
      query.state.data &&
      ["done", "error", "cancelled"].includes(query.state.data.status)
        ? false
        : 1000,
  });

  useEffect(() => {
    if (job?.status === "done" && job.draft) {
      setSuggestion(job.draft);
      setJobId(null);
    }
  }, [job]);

  const waiting = jobId !== null && job?.status !== "done";

  const diff = outputs?.diff;
  const bodyFor = (key: (typeof OUTPUT_TABS)[number]["key"]) => {
    if (!outputs) return "";
    if (key === "current") return outputs.current_full;
    if (key === "future") return outputs.future_full;
    if (key === "rollback") return outputs.diff.rollback_script;
    return outputs.diff.script;
  };

  return (
    <>
      <h1>Config 計画（現行 / 将来 / 差分）</h1>
      <p className="subtitle">
        1つの機器について「今こうなっている」と「こうしたい」を持ち、そこから3つを出します。
        <br />
        <strong>
          差分は、当てた結果が将来フルと同じ状態になることを機械で確認してから出します。
        </strong>
        確認が取れない差分はコマンドを出さず、どこが合わなかったかを表示します。
        <br />
        <strong>生成するだけで、実機には投入しません。</strong>
      </p>

      <div className="toolbar">
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "計画を作成"}
        </button>
      </div>

      {create.error && (
        <div className="error">{(create.error as Error).message}</div>
      )}

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="form-grid">
            <div className="field">
              <label htmlFor="device">対象の機器 *</label>
              <select
                id="device"
                required
                value={form.device_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, device_id: e.target.value }))
                }
              >
                <option value="">選択してください</option>
                {(devices ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.hostname}（{d.model}）
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="name">計画名 *</label>
              <input
                id="name"
                required
                placeholder="2027年度 更改後の構成"
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="seed">将来フルの初期値</label>
              <select
                id="seed"
                value={form.seed_from}
                onChange={(e) =>
                  setForm((f) => ({ ...f, seed_from: e.target.value }))
                }
              >
                <option value="current">現行 config をコピーして直す</option>
                <option value="design">
                  図に描いた構成から起こす（新規構築・テスト）
                </option>
                <option value="blank">白紙から書く</option>
              </select>
            </div>
          </div>
          <div className="actions">
            <button
              className="primary"
              type="submit"
              disabled={create.isPending}
            >
              作成
            </button>
          </div>
        </form>
      )}

      {(plans ?? []).length === 0 ? (
        <div className="empty">計画がありません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>計画</th>
              <th>対象機器</th>
              <th>版</th>
              <th>更新</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(plans ?? []).map((plan) => (
              <tr key={plan.id}>
                <td>
                  <strong>{plan.name}</strong>
                  {plan.description && (
                    <div className="muted">{plan.description}</div>
                  )}
                </td>
                <td>{plan.device_hostname ?? "—"}</td>
                <td>r{plan.revision}</td>
                <td className="muted">{formatTimestamp(plan.updated_at)}</td>
                <td>
                  <button
                    onClick={() => {
                      setSelectedId(plan.id);
                      setEditing(false);
                      setTab("diff");
                    }}
                  >
                    開く
                  </button>{" "}
                  <button
                    className="danger"
                    onClick={() => remove.mutate(plan.id)}
                  >
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && outputs && (
        <>
          <h2>
            {selected.name}（{outputs.device_hostname ?? "機器不明"}）
          </h2>

          {outputs.notes.map((note) => (
            <div
              key={note}
              className="card"
              style={{ marginTop: 8, borderColor: "var(--sev-warning)" }}
            >
              {note}
            </div>
          ))}

          <div className="toolbar" style={{ marginTop: 12 }}>
            {OUTPUT_TABS.map((item) => (
              <button
                key={item.key}
                className={tab === item.key ? "primary" : ""}
                onClick={() => setTab(item.key)}
              >
                {item.label}
              </button>
            ))}
            <button onClick={() => setEditing((v) => !v)}>
              {editing ? "編集をやめる" : "将来フルを編集"}
            </button>
          </div>

          <p className="muted">
            {OUTPUT_TABS.find((t) => t.key === tab)?.hint}
          </p>

          {editing ? (
            <>
              <textarea
                rows={24}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                style={{ width: "100%", fontFamily: "monospace" }}
              />
              <details
                className="card"
                style={{ marginTop: 12 }}
                open={suggestion !== null}
              >
                <summary>資料から下書きする</summary>
                <p className="muted">
                  取り込んだベンダー資料の中から、やりたいことに関係する箇所を探し、
                  それを根拠に投入コマンドの下書きを作ります。
                  {llm?.configured
                    ? "資料に同じ書き方が見つからない行には印を付けます。"
                    : "モデルを置いていないので、資料から拾ったコマンドを並べるところまでです。"}
                </p>
                <div className="actions">
                  <input
                    style={{ flex: 1 }}
                    placeholder="例: VLAN 120 を作って Gi0/1 に乗せる"
                    value={wish}
                    onChange={(e) => setWish(e.target.value)}
                  />
                  <button
                    className="primary"
                    onClick={() => suggest.mutate()}
                    disabled={!wish.trim() || suggest.isPending || waiting}
                  >
                    下書きを作る
                  </button>
                  {waiting && (
                    <>
                      <span className="muted">
                        作っています…（{Math.round(job?.seconds ?? 0)} 秒）。
                        この画面は触れます
                      </span>
                      <button
                        onClick={() => {
                          if (jobId) api.cancelJob(jobId);
                          setJobId(null);
                        }}
                      >
                        待つのをやめる
                      </button>
                    </>
                  )}
                </div>
                {suggest.error && (
                  <div className="error">
                    {(suggest.error as Error).message}
                  </div>
                )}
                {job?.status === "error" && (
                  <div className="error">
                    下書きを作れませんでした: {job.error}
                  </div>
                )}

                {/* 端末に何も入れられない職場では、会社が承認している AI を人が
                    使うほうが現実的。生成だけをそちらへ出し、検算はここに残す */}
                <details style={{ marginTop: 10 }}>
                  <summary>手元にモデルが無い（承認済みの AI に貼る）</summary>
                  <div className="actions">
                    <button
                      onClick={() => askForPrompt.mutate()}
                      disabled={!wish.trim() || askForPrompt.isPending}
                    >
                      貼る文を作る
                    </button>
                    {promptText && (
                      <button
                        onClick={() =>
                          navigator.clipboard?.writeText(promptText)
                        }
                      >
                        クリップボードにコピー
                      </button>
                    )}
                  </div>
                  {promptText && (
                    <>
                      <pre
                        className="diff"
                        style={{ maxHeight: 220, overflow: "auto" }}
                      >
                        {promptText}
                      </pre>
                      <p className="muted">
                        この文を AI
                        に貼り、返ってきた設定を下に貼り戻してください。
                        資料との突き合わせと、通信に影響する行の印は、ここで付けます。
                        実機の情報を外へ出すことになるので、職場の決まりを確かめてください。
                      </p>
                    </>
                  )}
                  <textarea
                    rows={6}
                    value={pasted}
                    placeholder="AI から返ってきた設定をここに貼る"
                    onChange={(e) => setPasted(e.target.value)}
                    style={{ width: "100%", fontFamily: "monospace" }}
                  />
                  <div className="actions">
                    <button
                      className="primary"
                      onClick={() => check.mutate()}
                      disabled={!pasted.trim() || check.isPending}
                    >
                      貼った設定を検める
                    </button>
                  </div>
                  {check.error && (
                    <div className="error">
                      {(check.error as Error).message}
                    </div>
                  )}
                </details>
                {suggestion && (
                  <>
                    {suggestion.notes.map((note) => (
                      <div
                        key={note}
                        className="muted"
                        style={{ marginTop: 6 }}
                      >
                        {note}
                      </div>
                    ))}
                    {suggestion.risks.length > 0 && (
                      <div
                        className="card"
                        style={{
                          marginTop: 8,
                          borderColor: "var(--sev-critical)",
                        }}
                      >
                        <strong>通信・接続に影響します</strong>
                        <ul>
                          {suggestion.risks.map((r) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {suggestion.lines.length > 0 && (
                      <pre className="diff" style={{ marginTop: 8 }}>
                        {suggestion.lines.map((line, i) => (
                          <div
                            key={i}
                            style={{
                              color: line.grounded
                                ? undefined
                                : "var(--sev-warning)",
                            }}
                            title={
                              line.grounded
                                ? "資料に同じ書き方があります"
                                : "資料に裏付けがありません"
                            }
                          >
                            {line.grounded ? "  " : "? "}
                            {line.text}
                          </div>
                        ))}
                      </pre>
                    )}
                    {suggestion.sources.length > 0 && (
                      <p className="muted">
                        根拠:{" "}
                        {suggestion.sources
                          .map((s) =>
                            s.heading ? `${s.title} / ${s.heading}` : s.title,
                          )
                          .join("、")}
                        {suggestion.model && `（下書き: ${suggestion.model}）`}
                      </p>
                    )}
                    {suggestion.body && (
                      <div className="actions">
                        <button
                          onClick={() =>
                            setDraft(
                              (current) =>
                                `${current.replace(/\s*$/, "")}\n${suggestion.body}\n`,
                            )
                          }
                        >
                          将来フルの末尾に貼る
                        </button>
                      </div>
                    )}
                  </>
                )}
              </details>

              <div className="actions">
                <button
                  className="primary"
                  onClick={() => save.mutate()}
                  disabled={save.isPending}
                >
                  保存して差分を作り直す
                </button>
                <button
                  onClick={() => {
                    setDraft(selected.body);
                    setEditing(false);
                  }}
                >
                  取り消す
                </button>
              </div>
            </>
          ) : (
            <>
              {tab === "diff" && diff && (
                <>
                  {diff.verified ? (
                    <div
                      className="card"
                      style={{ borderColor: "var(--sev-ok)" }}
                    >
                      ✓
                      この差分を当てると将来フルと同じ状態になることを確認しました
                      （追加 {diff.added} / 変更 {diff.changed} / 削除{" "}
                      {diff.removed}）
                    </div>
                  ) : (
                    <div
                      className="card"
                      style={{ borderColor: "var(--sev-expired)" }}
                    >
                      <strong>
                        差分が将来フルに収束しないため、コマンドを出していません。
                      </strong>
                      <ul>
                        {diff.mismatches.map((m) => (
                          <li key={m}>{m}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {diff.risks.length > 0 && (
                    <div
                      className="card"
                      style={{
                        marginTop: 8,
                        borderColor: "var(--sev-critical)",
                      }}
                    >
                      <strong>通信・接続に影響します</strong>
                      <ul>
                        {diff.risks.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {diff.warnings.map((w) => (
                    <div
                      key={w}
                      className="card"
                      style={{
                        marginTop: 8,
                        borderColor: "var(--sev-warning)",
                      }}
                    >
                      {w}
                    </div>
                  ))}

                  {diff.commands.length > 0 && (
                    <pre className="diff" style={{ marginTop: 12 }}>
                      {diff.commands.map((c, i) => (
                        <div key={i} style={{ color: KIND_COLOR[c.kind] }}>
                          {c.rendered}
                        </div>
                      ))}
                    </pre>
                  )}
                  {diff.commands.length === 0 && diff.verified && (
                    <div className="empty">現行と将来に差はありません</div>
                  )}
                </>
              )}

              {tab !== "diff" && (
                <pre className="diff">{bodyFor(tab) || "（空です）"}</pre>
              )}

              <div className="actions">
                <a
                  className="button"
                  href={api.planDownloadUrl(selected.id, tab)}
                  onClick={(e) => {
                    if (tab === "diff" && !diff?.verified) {
                      e.preventDefault();
                    }
                  }}
                >
                  ファイルで落とす
                </a>
                <button
                  onClick={() => navigator.clipboard?.writeText(bodyFor(tab))}
                >
                  クリップボードにコピー
                </button>
              </div>
              <p className="muted">
                内容を確認してから、手で機器に投入してください（このツールから投入はしません）。
              </p>
            </>
          )}
        </>
      )}
    </>
  );
}
