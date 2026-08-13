import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { ScenarioStatus } from "../api/types";
import QueryError from "../components/QueryError";
import { formatDate } from "../lib/format";

export const SCENARIO_STATUS_LABEL: Record<ScenarioStatus, string> = {
  draft: "検討中",
  review: "稟議中",
  approved: "承認済み",
  archived: "見送り / 完了",
};

export default function ScenariosPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: "", description: "", target_date: "" });

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["scenarios"],
    queryFn: api.listScenarios,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createScenario({
        name: form.name,
        description: form.description || null,
        target_date: form.target_date || null,
      }),
    onSuccess: () => {
      setForm({ name: "", description: "", target_date: "" });
      queryClient.invalidateQueries({ queryKey: ["scenarios"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteScenario(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scenarios"] }),
  });

  return (
    <>
      <h1>将来構成シナリオ</h1>
      <p className="subtitle">
        「この機器を入れ替えたらどうなるか」を、実際の台帳に触れずに試算します。
        台数・年間保守費・サポート切れ台数を現状と並べて比較できます。
      </p>

      {(error || create.error) && (
        <QueryError error={error ?? create.error} onRetry={error ? () => refetch() : undefined} />
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="toolbar">
          <input
            placeholder="シナリオ名（例: 2027年度 コア更改案）"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            style={{ minWidth: 280 }}
          />
          <input
            placeholder="説明（任意）"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            style={{ minWidth: 240 }}
          />
          <input
            type="date"
            title="いつ時点の構成として評価するか"
            value={form.target_date}
            onChange={(e) => setForm({ ...form, target_date: e.target.value })}
          />
          <button className="primary" type="submit" disabled={create.isPending}>
            作成
          </button>
        </div>
      </form>

      {isLoading ? (
        <p className="muted">読み込み中…</p>
      ) : (data ?? []).length === 0 ? (
        <div className="empty">シナリオがありません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>シナリオ</th>
              <th>状態</th>
              <th>評価時点</th>
              <th>項目数</th>
              <th>説明</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((scenario) => (
              <tr key={scenario.id}>
                <td>
                  <Link to={`/scenarios/${scenario.id}`}>{scenario.name}</Link>
                </td>
                <td>{SCENARIO_STATUS_LABEL[scenario.status]}</td>
                <td className="muted">
                  {scenario.target_date ? formatDate(scenario.target_date) : "現時点"}
                </td>
                <td>{scenario.item_count}</td>
                <td className="muted" style={{ whiteSpace: "normal", maxWidth: 360 }}>
                  {scenario.description ?? "—"}
                </td>
                <td>
                  <button
                    className="danger"
                    onClick={() => {
                      if (confirm(`「${scenario.name}」を削除しますか？`)) remove.mutate(scenario.id);
                    }}
                  >
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
