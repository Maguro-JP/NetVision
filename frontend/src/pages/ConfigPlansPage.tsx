import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ConfigPlan, DiffCommand } from "../api/types";

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
  const [form, setForm] = useState({ device_id: "", name: "", seed_from: "current" });

  const { data: plans } = useQuery({ queryKey: ["configPlans"], queryFn: () => api.listPlans() });
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
        <strong>差分は、当てた結果が将来フルと同じ状態になることを機械で確認してから出します。</strong>
        確認が取れない差分はコマンドを出さず、どこが合わなかったかを表示します。
        <br />
        <strong>生成するだけで、実機には投入しません。</strong>
      </p>

      <div className="toolbar">
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "計画を作成"}
        </button>
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
              <label htmlFor="device">対象の機器 *</label>
              <select
                id="device"
                required
                value={form.device_id}
                onChange={(e) => setForm((f) => ({ ...f, device_id: e.target.value }))}
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
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="field">
              <label htmlFor="seed">将来フルの初期値</label>
              <select
                id="seed"
                value={form.seed_from}
                onChange={(e) => setForm((f) => ({ ...f, seed_from: e.target.value }))}
              >
                <option value="current">現行 config をコピーして直す</option>
                <option value="design">図に描いた構成から起こす（新規構築・テスト）</option>
                <option value="blank">白紙から書く</option>
              </select>
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={create.isPending}>
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
                  {plan.description && <div className="muted">{plan.description}</div>}
                </td>
                <td>{plan.device_hostname ?? "—"}</td>
                <td>r{plan.revision}</td>
                <td className="muted">{new Date(plan.updated_at).toLocaleString("ja-JP")}</td>
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
                  <button className="danger" onClick={() => remove.mutate(plan.id)}>
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
            <div key={note} className="card" style={{ marginTop: 8, borderColor: "var(--sev-warning)" }}>
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

          <p className="muted">{OUTPUT_TABS.find((t) => t.key === tab)?.hint}</p>

          {editing ? (
            <>
              <textarea
                rows={24}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                style={{ width: "100%", fontFamily: "monospace" }}
              />
              <div className="actions">
                <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
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
                    <div className="card" style={{ borderColor: "var(--sev-ok)" }}>
                      ✓ この差分を当てると将来フルと同じ状態になることを確認しました
                      （追加 {diff.added} / 変更 {diff.changed} / 削除 {diff.removed}）
                    </div>
                  ) : (
                    <div className="card" style={{ borderColor: "var(--sev-expired)" }}>
                      <strong>差分が将来フルに収束しないため、コマンドを出していません。</strong>
                      <ul>
                        {diff.mismatches.map((m) => (
                          <li key={m}>{m}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {diff.risks.length > 0 && (
                    <div className="card" style={{ marginTop: 8, borderColor: "var(--sev-critical)" }}>
                      <strong>通信・接続に影響します</strong>
                      <ul>
                        {diff.risks.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {diff.warnings.map((w) => (
                    <div key={w} className="card" style={{ marginTop: 8, borderColor: "var(--sev-warning)" }}>
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

              {tab !== "diff" && <pre className="diff">{bodyFor(tab) || "（空です）"}</pre>}

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
                <button onClick={() => navigator.clipboard?.writeText(bodyFor(tab))}>
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
