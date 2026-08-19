import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { DocumentType } from "../api/types";
import { DOC_TYPE_LABEL, formatBytes, versionRange } from "../lib/docs";
import { formatDate } from "../lib/format";
import TemplateDraftsFromDocument from "../components/TemplateDraftsFromDocument";

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
    queryFn: () =>
      api.listDocuments({ q: q || undefined, doc_type: docType || undefined }),
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["documents"] });
  // 資料からテンプレートを起こす対象。開いている資料の ID
  const [draftsFor, setDraftsFor] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createDocument(form as unknown as Record<string, string>, file),
    onSuccess: () => {
      setForm({ ...EMPTY_FORM });
      setFile(null);
      setShowForm(false);
      refresh();
    },
  });

  const supersede = useMutation({
    mutationFn: ({ from, to }: { from: number; to: number }) =>
      api.supersedeDocument(from, to),
    onSuccess: () => {
      setSupersedeFrom(null);
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteDocument(id),
    onSuccess: refresh,
  });

  const set =
    (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
      setForm((prev) => ({ ...prev, [key]: e.target.value }));

  return (
    <>
      <h1>メーカー資料</h1>
      <p className="subtitle">
        コマンドリファレンスやコンフィグガイドを、
        <strong>メーカー・型番・バージョン範囲</strong>
        で管理します。渡した資料はこちらで章に切り、章ごとに出てくるコマンドを控えるので、
        下の「資料の中を探す」から書き方を引けます（閉域のまま、外の検索には出しません）。
        <br />
        <strong>古い版は消しません。</strong>
        新しい版を登録しても旧版は残り、そのバージョンで動いている機器からは旧版が引かれます。
      </p>

      <DocumentSearch />

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
              <label htmlFor="vendor">ベンダー *</label>
              <input
                id="vendor"
                required
                value={form.vendor}
                onChange={set("vendor")}
              />
            </div>
            <div className="field">
              <label htmlFor="model">
                型番（空ならシリーズ全体。`C9500*` で前方一致）
              </label>
              <input id="model" value={form.model} onChange={set("model")} />
            </div>
            <div className="field">
              <label htmlFor="doc_type">種別</label>
              <select
                id="doc_type"
                value={form.doc_type}
                onChange={set("doc_type")}
              >
                {Object.entries(DOC_TYPE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="title">タイトル *</label>
              <input
                id="title"
                required
                value={form.title}
                onChange={set("title")}
              />
            </div>
            <div className="field">
              <label htmlFor="os_name">OS名（IOS-XE など。空なら不問）</label>
              <input
                id="os_name"
                value={form.os_name}
                onChange={set("os_name")}
              />
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
              <input
                id="edition"
                value={form.edition}
                onChange={set("edition")}
              />
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
              <label htmlFor="source_url">
                入手元URL（実体が無くても記録しておく）
              </label>
              <input
                id="source_url"
                value={form.source_url}
                onChange={set("source_url")}
              />
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
            <button
              className="primary"
              type="submit"
              disabled={create.isPending}
            >
              登録
            </button>
          </div>
        </form>
      )}

      {supersedeFrom !== null && (
        <div
          className="card"
          style={{ marginTop: 12, borderColor: "var(--accent)" }}
        >
          後継として指定する資料を選んでください（旧版は削除されず、リンクされるだけです）
          <div className="actions">
            <select
              onChange={(e) => {
                if (e.target.value) {
                  supersede.mutate({
                    from: supersedeFrom,
                    to: Number(e.target.value),
                  });
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
                <td>
                  {versionRange(
                    doc.applies_from,
                    doc.applies_to,
                    doc.version_label,
                  )}
                </td>
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
                  <button onClick={() => setDraftsFor(doc.id)}>
                    テンプレートを起こす
                  </button>{" "}
                  <button onClick={() => setSupersedeFrom(doc.id)}>
                    後継を指定
                  </button>{" "}
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

      {draftsFor !== null && (
        <TemplateDraftsFromDocument
          documentId={draftsFor}
          title={
            (documents ?? []).find((doc) => doc.id === draftsFor)?.title ??
            "資料"
          }
          onClose={() => setDraftsFor(null)}
        />
      )}
    </>
  );
}

/**
 * 取り込んだ資料の中を探すところ。
 *
 * 資料は「置いてある」だけでは使えない。どの機種のどのコマンドをどう書くのか、
 * その場で引けて初めて現場で開かれる。探す先は取り込んだ資料の中だけで、
 * 外へは出さない。
 */
function DocumentSearch() {
  const [text, setText] = useState("");
  const [asked, setAsked] = useState("");
  const hits = useQuery({
    queryKey: ["documentSearch", asked],
    queryFn: () => api.searchDocuments({ q: asked }),
    enabled: asked.length > 0,
  });

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: "1rem" }}>資料の中を探す</h2>
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setAsked(text.trim());
        }}
      >
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="例: link-aggregation / VLAN 作成 / ntp"
          style={{ minWidth: 320, flex: 1 }}
        />
        <button className="primary" type="submit" disabled={!text.trim()}>
          探す
        </button>
      </form>

      {asked && hits.isLoading && <p className="muted">探しています…</p>}
      {asked && hits.data?.length === 0 && (
        <p className="muted">
          見つかりませんでした。資料を取り込むと、その中身から探せるようになります。
        </p>
      )}
      {(hits.data ?? []).map((hit, index) => (
        <div
          key={`${hit.document_id}-${index}`}
          className="card"
          style={{ marginTop: 8 }}
        >
          <div className="muted" style={{ fontSize: 12 }}>
            {hit.vendor}
            {hit.model ? ` / ${hit.model}` : ""} ・ {hit.title}
          </div>
          {hit.heading && <strong>{hit.heading}</strong>}
          {hit.commands.length > 0 && (
            <pre className="config-block" style={{ marginTop: 6 }}>
              {hit.commands.join("\n")}
            </pre>
          )}
          <p
            className="muted"
            style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
          >
            {hit.body.slice(0, 300)}
          </p>
        </div>
      ))}
    </div>
  );
}
