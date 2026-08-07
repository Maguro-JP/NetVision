import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { ConfigTemplate, TemplateCategory } from "../api/types";
import { TEMPLATE_CATEGORY_LABEL, versionRange } from "../lib/docs";

const SAMPLE_BODY = `vlan {{ vlan_id }}
 name {{ vlan_name }}
!
interface {{ interface }}
 description {{ vlan_name }} ({{ device.hostname }})
 switchport mode access
 switchport access vlan {{ vlan_id }}
`;

const EMPTY_FORM = {
  name: "",
  description: "",
  category: "vlan" as TemplateCategory,
  vendor: "",
  model: "",
  os_name: "",
  applies_from: "",
  applies_to: "",
  version_label: "",
  body: SAMPLE_BODY,
};

export default function ConfigTemplatesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [showForm, setShowForm] = useState(false);
  const [selected, setSelected] = useState<ConfigTemplate | null>(null);
  const [deviceId, setDeviceId] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});

  const { data: templates } = useQuery({
    queryKey: ["configTemplates"],
    queryFn: () => api.listTemplates(),
  });
  const { data: devices } = useQuery({
    queryKey: ["devices", {}],
    queryFn: () => api.listDevices(),
  });
  const { data: documents } = useQuery({
    queryKey: ["documents", "", ""],
    queryFn: () => api.listDocuments(),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["configTemplates"] });

  const create = useMutation({
    mutationFn: () =>
      api.createTemplate({
        ...form,
        model: form.model || null,
        os_name: form.os_name || null,
        applies_from: form.applies_from || null,
        applies_to: form.applies_to || null,
        version_label: form.version_label || null,
        description: form.description || null,
        variables: [],
      }),
    onSuccess: () => {
      setForm({ ...EMPTY_FORM });
      setShowForm(false);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteTemplate(id),
    onSuccess: () => {
      setSelected(null);
      refresh();
    },
  });

  const render = useMutation({
    mutationFn: () =>
      api.renderTemplate(selected!.id, {
        device_id: deviceId ? Number(deviceId) : undefined,
        variables,
      }),
  });

  const set = (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const pick = (template: ConfigTemplate) => {
    setSelected(template);
    setVariables({});
    render.reset();
  };

  return (
    <>
      <h1>Config テンプレート</h1>
      <p className="subtitle">
        コマンドの書式はメーカー・型番・バージョンで変わるので、資料と同じ3軸で管理します。
        機器を選ぶと、その機器に合う書式のテンプレートで生成できます。
        <br />
        <strong>生成するだけで、実機には投入しません。</strong>
        内容を確認して手で流し込んでください。
      </p>

      <div className="toolbar">
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "テンプレートを作成"}
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
              <label htmlFor="name">テンプレート名 *</label>
              <input id="name" required value={form.name} onChange={set("name")} />
            </div>
            <div className="field">
              <label htmlFor="category">分類</label>
              <select id="category" value={form.category} onChange={set("category")}>
                {Object.entries(TEMPLATE_CATEGORY_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="vendor">ベンダー *</label>
              <input id="vendor" required value={form.vendor} onChange={set("vendor")} />
            </div>
            <div className="field">
              <label htmlFor="model">型番（空ならベンダー全体）</label>
              <input id="model" value={form.model} onChange={set("model")} />
            </div>
            <div className="field">
              <label htmlFor="os_name">OS名</label>
              <input id="os_name" value={form.os_name} onChange={set("os_name")} />
            </div>
            <div className="field">
              <label htmlFor="applies_from">適用バージョン（開始）</label>
              <input id="applies_from" value={form.applies_from} onChange={set("applies_from")} />
            </div>
            <div className="field">
              <label htmlFor="applies_to">適用バージョン（終了）</label>
              <input id="applies_to" value={form.applies_to} onChange={set("applies_to")} />
            </div>
            <div className="field wide">
              <label htmlFor="description">説明</label>
              <input id="description" value={form.description} onChange={set("description")} />
            </div>
            <div className="field wide">
              <label htmlFor="body">
                本文（<code>{"{{ 変数名 }}"}</code> で差し込み。
                <code>device.hostname</code> など機器の値も使えます）
              </label>
              <textarea
                id="body"
                rows={12}
                required
                value={form.body}
                onChange={set("body")}
                style={{ fontFamily: "monospace" }}
              />
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={create.isPending}>
              作成
            </button>
          </div>
        </form>
      )}

      <h2>テンプレート一覧</h2>
      {(templates ?? []).length === 0 ? (
        <div className="empty">テンプレートがありません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>名前</th>
              <th>分類</th>
              <th>対象</th>
              <th>適用バージョン</th>
              <th>根拠資料</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(templates ?? []).map((template) => (
              <tr key={template.id}>
                <td>
                  <strong>{template.name}</strong>
                  {template.description && <div className="muted">{template.description}</div>}
                </td>
                <td>{TEMPLATE_CATEGORY_LABEL[template.category]}</td>
                <td>
                  {template.vendor}
                  <div className="muted">
                    {template.model ?? "全機種"}
                    {template.os_name ? ` / ${template.os_name}` : ""}
                  </div>
                </td>
                <td>
                  {versionRange(
                    template.applies_from,
                    template.applies_to,
                    template.version_label,
                  )}
                </td>
                <td className="muted">{template.source_document_title ?? "—"}</td>
                <td>
                  <button onClick={() => pick(template)}>生成</button>{" "}
                  <button className="danger" onClick={() => remove.mutate(template.id)}>
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <>
          <h2>「{selected.name}」から生成</h2>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="device">対象の機器</label>
              <select id="device" value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
                <option value="">指定しない</option>
                {(devices ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.hostname}（{d.model} / {d.os_version ?? "版数不明"}）
                  </option>
                ))}
              </select>
            </div>
            {selected.detected_variables.map((variable) => (
              <div className="field" key={variable.name}>
                <label htmlFor={`var-${variable.name}`}>
                  {variable.label || variable.name}
                  {variable.required === false ? "" : " *"}
                </label>
                <input
                  id={`var-${variable.name}`}
                  value={variables[variable.name] ?? ""}
                  placeholder={variable.default ?? ""}
                  onChange={(e) =>
                    setVariables((prev) => ({ ...prev, [variable.name]: e.target.value }))
                  }
                />
                {variable.help && <span className="muted">{variable.help}</span>}
              </div>
            ))}
          </div>

          <div className="actions">
            <button className="primary" onClick={() => render.mutate()} disabled={render.isPending}>
              生成する
            </button>
            <button onClick={() => setSelected(null)}>閉じる</button>
          </div>

          {render.error && <div className="error">{(render.error as Error).message}</div>}

          {render.data && (
            <>
              {render.data.warnings.map((warning) => (
                <div
                  key={warning}
                  className="card"
                  style={{ marginTop: 12, borderColor: "var(--sev-warning)" }}
                >
                  {warning}
                </div>
              ))}
              <p className="muted" style={{ marginTop: 12 }}>
                内容を確認してから、手で機器に投入してください（このツールから投入はしません）。
              </p>
              <pre className="diff">{render.data.rendered}</pre>
              <div className="actions">
                <button
                  onClick={() => navigator.clipboard?.writeText(render.data!.rendered)}
                >
                  クリップボードにコピー
                </button>
              </div>
            </>
          )}
        </>
      )}

      {(documents ?? []).length === 0 && (
        <p className="muted" style={{ marginTop: 20 }}>
          テンプレートの根拠になる資料は「ベンダードキュメント」から登録できます。
        </p>
      )}
    </>
  );
}
