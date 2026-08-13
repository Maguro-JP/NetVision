import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import QueryError from "../components/QueryError";

export default function SitesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: "", address: "" });

  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["sites"], queryFn: api.listSites });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["sites"] });
    queryClient.invalidateQueries({ queryKey: ["devices"] });
  };

  const create = useMutation({
    mutationFn: () => api.createSite({ name: form.name, address: form.address || null }),
    onSuccess: () => {
      setForm({ name: "", address: "" });
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSite(id),
    onSuccess: invalidate,
  });

  return (
    <>
      <h1>拠点</h1>
      <p className="subtitle">機器の設置場所を登録します。削除しても機器は残ります。</p>

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
            placeholder="拠点名"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <input
            placeholder="住所（任意）"
            value={form.address}
            onChange={(e) => setForm({ ...form, address: e.target.value })}
            style={{ minWidth: 240 }}
          />
          <button className="primary" type="submit" disabled={create.isPending}>
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
              <th>拠点名</th>
              <th>住所</th>
              <th>機器数</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((site) => (
              <tr key={site.id}>
                <td>
                  <Link to={`/devices?site_id=${site.id}`}>{site.name}</Link>
                  {" "}
                  <Link
                    to={`/topology?site=${site.id}`}
                    className="muted"
                    title="この拠点だけの構成図を開きます"
                  >
                    図で見る
                  </Link>
                </td>
                <td className="muted">{site.address ?? "—"}</td>
                <td className="num">{site.device_count}</td>
                <td>
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
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
