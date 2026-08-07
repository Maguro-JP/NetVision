import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { DocumentType } from "../api/types";
import { DOC_TYPE_LABEL, formatBytes, versionRange } from "../lib/docs";
import { formatDate } from "../lib/format";

const EMPTY_FORM = {
  vendor: "",
  model: "",
  os_name: "",
  doc_type: "command_reference" as DocumentType,
  title: "",
  applies_from: "",
  applies_to: "",
  version_label: "",
  edition: "",
  published_at: "",
  source_url: "",
  notes: "",
};

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [file, setFile] = useState<File | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [q, setQ] = useState("");
  const [docType, setDocType] = useState("");
  const [supersedeFrom, setSupersedeFrom] = useState<number | null>(null);

  const { data: documents } = useQuery({
    queryKey: ["documents", q, docType],
    queryFn: () => api.listDocuments({ q: q || undefined, doc_type: docType || undefined }),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents"] });

  const create = useMutation({
    mutationFn: () => api.createDocument(form as unknown as Record<string, string>, file),
    onSuccess: () => {
      setForm({ ...EMPTY_FORM });
      setFile(null);
      setShowForm(false);
      refresh();
    },
  });

  const supersede = useMutation({
    mutationFn: ({ from, to }: { from: number; to: number }) => api.supersedeDocument(from, to),
    onSuccess: () => {
      setSupersedeFrom(null);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteDocument(id),
    onSuccess: refresh,
  });

  const set = (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  return (
    <>
      <h1>ベンダードキュメント</h1>
      <p className="subtitle">
        コマンドリファレンスやコンフィグガイドを、<strong>ベンダー・型番・バージョン範囲</strong>
        で管理します。
        <br />
        <strong>古い版は消しません。</strong>
        新しい版を登録しても旧版は残り、そのバージョンで動いている機器からは旧版が引かれます。
      </p>

      <div className="toolbar">
        <input
          placeholder="タイトル / 型番 / 備考で検索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 260 }}
        />
        <select value={docType} onChange={(e) => setDocType(e.target.value)}>
          <option value="">すべての種別</option>
          {Object.entries(DOC_TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "資料を登録"}
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
              <label htmlFor="vendor">ベンダー *</label>
              <input id="vendor" required value={form.vendor} onChange={set("vendor")} />
            </div>
            <div className="field">
              <label htmlFor="model">型番（空ならシリーズ全体。`C9500*` で前方一致）</label>
              <input id="model" value={form.model} onChange={set("model")} />
            </div>
            <div className="field">
              <label htmlFor="doc_type">種別</label>
              <select id="doc_type" value={form.doc_type} onChange={set("doc_type")}>
                {Object.entries(DOC_TYPE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="title">タイトル *</label>
              <input id="title" required value={form.title} onChange={set("title")} />
            </div>
            <div className="field">
              <label htmlFor="os_name">OS名（IOS-XE など。空なら不問）</label>
              <input id="os_name" value={form.os_name} onChange={set("os_name")} />
            </div>
            <div className="field">
              <label htmlFor="applies_from">適用バージョン（開始）</label>
              <input
                id="applies_from"
                placeholder="17.9.0"
                value={form.applies_from}
                onChange={set("applies_from")}
              />
            </div>
            <div className="field">
              <label htmlFor="applies_to">適用バージョン（終了）</label>
              <input
                id="applies_to"
                placeholder="17.9.99"
                value={form.applies_to}
                onChange={set("applies_to")}
              />
            </div>
            <div className="field">
              <label htmlFor="version_label">表示用のバージョン表記</label>
              <input
                id="version_label"
                placeholder="17.9.x"
                value={form.version_label}
                onChange={set("version_label")}
              />
            </div>
            <div className="field">
              <label htmlFor="edition">資料の版（Rev.C など）</label>
              <input id="edition" value={form.edition} onChange={set("edition")} />
            </div>
            <div className="field">
              <label htmlFor="published_at">発行日</label>
              <input
                id="published_at"
                type="date"
                value={form.published_at}
                onChange={set("published_at")}
              />
            </div>
            <div className="field wide">
              <label htmlFor="source_url">入手元URL（実体が無くても記録しておく）</label>
              <input id="source_url" value={form.source_url} onChange={set("source_url")} />
            </div>
            <div className="field wide">
              <label htmlFor="file">ファイル（PDF など。省略可）</label>
              <input
                id="file"
                type="file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="field wide">
              <label htmlFor="notes">備考</label>
              <input id="notes" value={form.notes} onChange={set("notes")} />
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={create.isPending}>
              登録
            </button>
          </div>
        </form>
      )}

      {supersedeFrom !== null && (
        <div className="card" style={{ marginTop: 12, borderColor: "var(--accent)" }}>
          後継として指定する資料を選んでください（旧版は削除されず、リンクされるだけです）
          <div className="actions">
            <select
              onChange={(e) => {
                if (e.target.value) {
                  supersede.mutate({ from: supersedeFrom, to: Number(e.target.value) });
                }
              }}
              defaultValue=""
            >
              <option value="">選択してください</option>
              {(documents ?? [])
                .filter((d) => d.id !== supersedeFrom)
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
            </select>
            <button onClick={() => setSupersedeFrom(null)}>やめる</button>
          </div>
        </div>
      )}

      {(documents ?? []).length === 0 ? (
        <div className="empty">資料が登録されていません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>資料</th>
              <th>対象</th>
              <th>適用バージョン</th>
              <th>該当機器</th>
              <th>発行 / 入手</th>
              <th>実体</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(documents ?? []).map((doc) => (
              <tr key={doc.id}>
                <td style={{ whiteSpace: "normal", maxWidth: 320 }}>
                  <strong>{doc.title}</strong>
                  <div className="muted">
                    {DOC_TYPE_LABEL[doc.doc_type]}
                    {doc.edition ? ` / ${doc.edition}` : ""}
                  </div>
                  {doc.superseded_by_title && (
                    <div className="muted">後継: {doc.superseded_by_title}</div>
                  )}
                </td>
                <td>
                  {doc.vendor}
                  <div className="muted">
                    {doc.model ?? "シリーズ全体"}
                    {doc.os_name ? ` / ${doc.os_name}` : ""}
                  </div>
                </td>
                <td>{versionRange(doc.applies_from, doc.applies_to, doc.version_label)}</td>
                <td>{doc.device_count}</td>
                <td className="muted">
                  {formatDate(doc.published_at)}
                  {doc.source_url && (
                    <div>
                      <a href={doc.source_url} target="_blank" rel="noreferrer">
                        入手元
                      </a>
                    </div>
                  )}
                </td>
                <td>
                  {doc.has_file ? (
                    <a href={api.documentDownloadUrl(doc.id)}>
                      {formatBytes(doc.size_bytes)}
                    </a>
                  ) : (
                    <span className="muted">URLのみ</span>
                  )}
                </td>
                <td>
                  <button onClick={() => setSupersedeFrom(doc.id)}>後継を指定</button>{" "}
                  <button
                    className="danger"
                    onClick={() => {
                      if (
                        confirm(
                          `「${doc.title}」を完全に削除します。\n版が新しくなっただけなら「後継を指定」を使ってください。`,
                        )
                      ) {
                        remove.mutate(doc.id);
                      }
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
