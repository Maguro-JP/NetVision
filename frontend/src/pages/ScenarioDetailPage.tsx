import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { ScenarioAction, ScenarioStatus } from "../api/types";
import { formatCurrency, formatDate } from "../lib/format";
import { SCENARIO_STATUS_LABEL } from "./ScenariosPage";

const ACTION_LABEL: Record<ScenarioAction, string> = {
  replace: "入れ替え",
  add: "新規追加",
  remove: "撤去",
  keep: "据え置き",
};

const EMPTY_ITEM = {
  action: "add" as ScenarioAction,
  device_id: "",
  planned_vendor: "",
  planned_model: "",
  quantity: "1",
  unit_cost: "",
  planned_annual_cost: "",
  planned_install_date: "",
  planned_eol_date: "",
  notes: "",
};

/** 現状と将来を並べ、差分に符号と色を付けて見せる。 */
function CompareRow({
  label,
  current,
  projected,
  format = (v: number) => v.toLocaleString(),
  goodWhenLower = true,
}: {
  label: string;
  current: number | null;
  projected: number | null;
  format?: (v: number) => string;
  goodWhenLower?: boolean;
}) {
  const delta = current !== null && projected !== null ? projected - current : null;
  const improved = delta === null || delta === 0 ? null : goodWhenLower ? delta < 0 : delta > 0;

  return (
    <tr>
      <td>{label}</td>
      <td>{current === null ? "—" : format(current)}</td>
      <td>{projected === null ? "—" : format(projected)}</td>
      <td
        style={{
          color:
            improved === null ? "var(--muted)" : improved ? "var(--sev-ok)" : "var(--sev-expired)",
          fontWeight: 600,
        }}
      >
        {delta === null || delta === 0 ? "±0" : `${delta > 0 ? "+" : ""}${format(delta)}`}
      </td>
    </tr>
  );
}

export default function ScenarioDetailPage() {
  const { id } = useParams();
  const scenarioId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [item, setItem] = useState({ ...EMPTY_ITEM });
  const [showForm, setShowForm] = useState(false);
  const [withinDays, setWithinDays] = useState("730");

  const { data: scenario, error } = useQuery({
    queryKey: ["scenario", id],
    queryFn: () => api.getScenario(scenarioId),
  });
  const { data: summary } = useQuery({
    queryKey: ["scenarioSummary", id],
    queryFn: () => api.scenarioSummary(scenarioId),
  });
  const { data: devices } = useQuery({ queryKey: ["devices", {}], queryFn: () => api.listDevices() });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["scenario", id] });
    queryClient.invalidateQueries({ queryKey: ["scenarioSummary", id] });
    queryClient.invalidateQueries({ queryKey: ["scenarios"] });
  };

  const generate = useMutation({
    mutationFn: () => api.generateScenarioItems(scenarioId, Number(withinDays)),
    onSuccess: refresh,
  });

  const addItem = useMutation({
    mutationFn: () =>
      api.addScenarioItem(scenarioId, {
        action: item.action,
        device_id: item.device_id ? Number(item.device_id) : null,
        planned_vendor: item.planned_vendor || null,
        planned_model: item.planned_model || null,
        quantity: Number(item.quantity) || 1,
        unit_cost: item.unit_cost ? Number(item.unit_cost) : null,
        planned_annual_cost: item.planned_annual_cost ? Number(item.planned_annual_cost) : null,
        planned_install_date: item.planned_install_date || null,
        planned_eol_date: item.planned_eol_date || null,
        notes: item.notes || null,
      }),
    onSuccess: () => {
      setItem({ ...EMPTY_ITEM });
      setShowForm(false);
      refresh();
    },
  });

  const removeItem = useMutation({
    mutationFn: (itemId: number) => api.deleteScenarioItem(itemId),
    onSuccess: refresh,
  });

  const setStatus = useMutation({
    mutationFn: (status: ScenarioStatus) => api.updateScenario(scenarioId, { status }),
    onSuccess: refresh,
  });

  const removeScenario = useMutation({
    mutationFn: () => api.deleteScenario(scenarioId),
    onSuccess: () => {
      refresh();
      navigate("/scenarios");
    },
  });

  if (error) return <div className="error">{(error as Error).message}</div>;
  if (!scenario) return <p className="muted">読み込み中…</p>;

  const set = (key: keyof typeof EMPTY_ITEM) => (e: { target: { value: string } }) =>
    setItem((prev) => ({ ...prev, [key]: e.target.value }));

  return (
    <>
      <h1>{scenario.name}</h1>
      <p className="subtitle">
        <Link to="/scenarios">← シナリオ一覧</Link>
        {scenario.description ? ` / ${scenario.description}` : ""}
      </p>

      <div className="toolbar">
        <select
          value={scenario.status}
          onChange={(e) => setStatus.mutate(e.target.value as ScenarioStatus)}
        >
          {Object.entries(SCENARIO_STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="muted" style={{ alignSelf: "center" }}>
          評価時点: {summary ? formatDate(summary.evaluated_on) : "—"}
        </span>
        <button
          className="danger"
          onClick={() => {
            if (confirm(`「${scenario.name}」を削除しますか？`)) removeScenario.mutate();
          }}
        >
          シナリオを削除
        </button>
      </div>

      {summary && summary.warnings.length > 0 && (
        <div className="error">
          {summary.warnings.map((warning) => (
            <div key={warning}>{warning}</div>
          ))}
        </div>
      )}

      {summary && (
        <>
          <h2>現状との比較</h2>
          <div className="cards">
            <div className="card">
              <div className="label">初期投資（機器費用）</div>
              <div className="value">{formatCurrency(summary.investment)}</div>
            </div>
            <div className="card">
              <div className="label">年間保守費の増減</div>
              <div
                className="value"
                style={{
                  color: summary.annual_cost_delta <= 0 ? "var(--sev-ok)" : "var(--sev-critical)",
                }}
              >
                {summary.annual_cost_delta > 0 ? "+" : ""}
                {formatCurrency(summary.annual_cost_delta)}
              </div>
            </div>
          </div>

          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>指標</th>
                <th>現状</th>
                <th>この案の適用後</th>
                <th>差分</th>
              </tr>
            </thead>
            <tbody>
              <CompareRow
                label="機器台数"
                current={summary.current.device_count}
                projected={summary.projected.device_count}
                goodWhenLower={false}
                format={(v) => `${v.toLocaleString()} 台`}
              />
              <CompareRow
                label="年間保守費"
                current={summary.current.annual_maintenance_cost}
                projected={summary.projected.annual_maintenance_cost}
                format={formatCurrency}
              />
              <CompareRow
                label="サポート切れ（EoL超過）"
                current={summary.current.unsupported_count}
                projected={summary.projected.unsupported_count}
                format={(v) => `${v} 台`}
              />
              <CompareRow
                label="1年以内に期限を迎える"
                current={summary.current.at_risk_count}
                projected={summary.projected.at_risk_count}
                format={(v) => `${v} 台`}
              />
              <CompareRow
                label="平均経過年数"
                current={summary.current.average_age_years}
                projected={summary.projected.average_age_years}
                format={(v) => `${v.toFixed(1)} 年`}
              />
            </tbody>
          </table>
        </>
      )}

      <h2>案の内容</h2>
      <div className="toolbar">
        <span className="muted" style={{ alignSelf: "center" }}>
          期限が近い機器から入れ替え案を作る:
        </span>
        <select value={withinDays} onChange={(e) => setWithinDays(e.target.value)}>
          <option value="365">1年以内</option>
          <option value="730">2年以内</option>
          <option value="1095">3年以内</option>
        </select>
        <button onClick={() => generate.mutate()} disabled={generate.isPending}>
          たたき台を作る
        </button>
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "項目を追加"}
        </button>
      </div>

      {addItem.error && <div className="error">{(addItem.error as Error).message}</div>}

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addItem.mutate();
          }}
        >
          <div className="form-grid">
            <div className="field">
              <label htmlFor="action">操作</label>
              <select id="action" value={item.action} onChange={set("action")}>
                {Object.entries(ACTION_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="device">対象の機器（入れ替え・撤去のとき）</label>
              <select id="device" value={item.device_id} onChange={set("device_id")}>
                <option value="">指定しない</option>
                {(devices ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.hostname}（{d.model}）
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="vendor">導入するベンダー</label>
              <input id="vendor" value={item.planned_vendor} onChange={set("planned_vendor")} />
            </div>
            <div className="field">
              <label htmlFor="model">導入する型番</label>
              <input id="model" value={item.planned_model} onChange={set("planned_model")} />
            </div>
            <div className="field">
              <label htmlFor="quantity">台数</label>
              <input
                id="quantity"
                type="number"
                min={1}
                value={item.quantity}
                onChange={set("quantity")}
              />
            </div>
            <div className="field">
              <label htmlFor="unit_cost">単価（円）</label>
              <input
                id="unit_cost"
                type="number"
                min={0}
                value={item.unit_cost}
                onChange={set("unit_cost")}
              />
            </div>
            <div className="field">
              <label htmlFor="annual">保守年額（1台あたり・円）</label>
              <input
                id="annual"
                type="number"
                min={0}
                value={item.planned_annual_cost}
                onChange={set("planned_annual_cost")}
              />
            </div>
            <div className="field">
              <label htmlFor="install">導入予定日</label>
              <input
                id="install"
                type="date"
                value={item.planned_install_date}
                onChange={set("planned_install_date")}
              />
            </div>
            <div className="field">
              <label htmlFor="eol">想定EoL</label>
              <input
                id="eol"
                type="date"
                value={item.planned_eol_date}
                onChange={set("planned_eol_date")}
              />
            </div>
            <div className="field wide">
              <label htmlFor="notes">メモ</label>
              <input id="notes" value={item.notes} onChange={set("notes")} />
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={addItem.isPending}>
              追加
            </button>
          </div>
        </form>
      )}

      {scenario.items.length === 0 ? (
        <div className="empty">
          項目がありません。「たたき台を作る」で期限が近い機器から自動生成できます。
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>操作</th>
              <th>対象</th>
              <th>導入機器</th>
              <th>台数</th>
              <th>単価</th>
              <th>保守年額</th>
              <th>導入予定</th>
              <th>想定EoL</th>
              <th>メモ</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {scenario.items.map((row) => (
              <tr key={row.id}>
                <td>{ACTION_LABEL[row.action]}</td>
                <td>
                  {row.device_id ? (
                    <Link to={`/devices/${row.device_id}`}>{row.device_label}</Link>
                  ) : (
                    <span className="muted">{row.device_label ?? "—"}</span>
                  )}
                </td>
                <td>
                  {row.planned_vendor ? `${row.planned_vendor} ` : ""}
                  {row.planned_model ?? "—"}
                </td>
                <td>{row.quantity}</td>
                <td>{formatCurrency(row.unit_cost)}</td>
                <td>{formatCurrency(row.planned_annual_cost)}</td>
                <td className="muted">{formatDate(row.planned_install_date)}</td>
                <td className="muted">{formatDate(row.planned_eol_date)}</td>
                <td className="muted" style={{ whiteSpace: "normal", maxWidth: 200 }}>
                  {row.notes ?? "—"}
                </td>
                <td>
                  <button className="danger" onClick={() => removeItem.mutate(row.id)}>
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
