import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Site } from "../api/types";
import QueryError from "../components/QueryError";

type SiteForm = { code: string; name: string; address: string; notes: string };

const EMPTY: SiteForm = { code: "", name: "", address: "", notes: "" };

function toForm(site: Site): SiteForm {
  return {
    code: site.code ?? "",
    name: site.name,
    address: site.address ?? "",
    notes: site.notes ?? "",
  };
}

function toPayload(form: SiteForm): Partial<Site> {
  return {
    code: form.code.trim() || null,
    name: form.name.trim(),
    address: form.address.trim() || null,
    notes: form.notes.trim() || null,
  };
}

/**
 * 拠点の台帳。
 *
 * 機器と同じく、ここで登録・直し・削除する。拠点IDはプロジェクトが既に
 * 使っている番号をそのまま入れられる（内部の連番とは別）。
 */
export default function SitesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<SiteForm>(EMPTY);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<SiteForm>(EMPTY);

  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["sites"], queryFn: api.listSites });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["sites"] });
    queryClient.invalidateQueries({ queryKey: ["devices"] });
  };

  const create = useMutation({
    mutationFn: () => api.createSite(toPayload(form)),
    onSuccess: () => {
      setForm(EMPTY);
      invalidate();
    },
  });

  const update = useMutation({
    mutationFn: () => api.updateSite(editingId!, toPayload(draft)),
    onSuccess: () => {
      setEditingId(null);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSite(id),
    onSuccess: invalidate,
  });

  const startEdit = (site: Site) => {
    setEditingId(site.id);
    setDraft(toForm(site));
  };

  const saveError = create.error ?? update.error ?? remove.error;

  return (
    <>
      <h1>拠点</h1>
      <p className="subtitle">
        設置場所の台帳です。拠点IDはプロジェクトで使っている番号をそのまま入れられます。
        削除しても機器は残ります。
      </p>

      {(error || saveError) && (
        <QueryError
          error={error ?? saveError}
          onRetry={error ? () => refetch() : undefined}
        />
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="card"
        style={{ marginBottom: 16 }}
      >
        <h2 style={{ marginTop: 0, fontSize: "1rem" }}>拠点を追加</h2>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="new-site-code">拠点ID</label>
            <input
              id="new-site-code"
              placeholder="例: TOKYO-DC1"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              title="プロジェクトで使っている拠点の番号。空でも登録できます"
            />
          </div>
          <div className="field">
            <label htmlFor="new-site-name">拠点名 *</label>
            <input
              id="new-site-name"
              placeholder="例: 東京データセンター"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="new-site-address">住所</label>
            <input
              id="new-site-address"
              placeholder="任意"
              value={form.address}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
            />
          </div>
          <div className="field" style={{ gridColumn: "1 / -1" }}>
            <label htmlFor="new-site-notes">メモ</label>
            <input
              id="new-site-notes"
              placeholder="任意"
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </div>
        </div>
        <div className="toolbar" style={{ marginTop: 8 }}>
          <button className="primary" type="submit" disabled={create.isPending || !form.name.trim()}>
            追加
          </button>
        </div>
      </form>

      {isLoading ? (
        <p className="muted">読み込み中…</p>
      ) : (data ?? []).length === 0 ? (
        <div className="empty">拠点が登録されていません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>拠点ID</th>
              <th>拠点名</th>
              <th>住所</th>
              <th>メモ</th>
              <th>機器数</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((site) =>
              editingId === site.id ? (
                <tr key={site.id}>
                  <td>
                    <input
                      value={draft.code}
                      onChange={(e) => setDraft({ ...draft, code: e.target.value })}
                      placeholder="拠点ID"
                      aria-label="拠点ID"
                    />
                  </td>
                  <td>
                    <input
                      value={draft.name}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                      required
                      aria-label="拠点名"
                    />
                  </td>
                  <td>
                    <input
                      value={draft.address}
                      onChange={(e) => setDraft({ ...draft, address: e.target.value })}
                      aria-label="住所"
                    />
                  </td>
                  <td>
                    <input
                      value={draft.notes}
                      onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
                      aria-label="メモ"
                    />
                  </td>
                  <td className="num">{site.device_count}</td>
                  <td className="nowrap">
                    <button
                      className="primary"
                      disabled={update.isPending || !draft.name.trim()}
                      onClick={() => update.mutate()}
                    >
                      保存
                    </button>{" "}
                    <button type="button" onClick={() => setEditingId(null)}>
                      やめる
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={site.id}>
                  <td className="mono">{site.code ?? <span className="muted">—</span>}</td>
                  <td>
                    <Link to={`/devices?site_id=${site.id}`}>{site.name}</Link>{" "}
                    <Link
                      to={`/topology?site=${site.id}`}
                      className="muted"
                      title="この拠点だけの構成図を開きます"
                    >
                      図で見る
                    </Link>
                  </td>
                  <td className="muted">{site.address ?? "—"}</td>
                  <td className="muted">{site.notes ?? "—"}</td>
                  <td className="num">{site.device_count}</td>
                  <td className="nowrap">
                    <button type="button" onClick={() => startEdit(site)}>
                      直す
                    </button>{" "}
                    <button
                      className="danger"
                      onClick={() => {
                        if (confirm(`${site.name} を削除しますか？`)) remove.mutate(site.id);
                      }}
                    >
                      削除
                    </button>
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      )}
    </>
  );
}
