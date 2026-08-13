import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import ChangeLogTable, { ACTION_LABEL, ENTITY_LABEL, SOURCE_LABEL } from "../components/ChangeLogTable";
import QueryError from "../components/QueryError";
import { SEARCH_DELAY_MS, useDebouncedValue } from "../lib/debounce";

const PAGE_SIZE = 100;

export default function ChangesPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [qInput, setQInput] = useState("");
  const qDebounced = useDebouncedValue(qInput, SEARCH_DELAY_MS);
  const [page, setPage] = useState(0);

  const params = {
    ...filters,
    q: qDebounced || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["changes", params],
    queryFn: () => api.listChanges(params),
  });

  const setFilter = (key: string, value: string) => {
    setPage(0);
    setFilters((prev) => {
      const next = { ...prev };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
  };

  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <>
      <h1>変更履歴</h1>
      <p className="subtitle">
        機器・保守契約・拠点への変更をすべて記録しています。画面での編集も台帳の取り込みも、
        同じ履歴に残ります。
      </p>

      <div className="toolbar">
        <input
          type="search"
          placeholder="対象名 / 変更内容で検索"
          value={qInput}
          onChange={(e) => {
            setPage(0);
            setQInput(e.target.value);
          }}
          style={{ minWidth: 260 }}
        />
        {isFetching && !isLoading && <span className="muted">探しています…</span>}
        <select
          value={filters.entity_type ?? ""}
          onChange={(e) => setFilter("entity_type", e.target.value)}
        >
          <option value="">すべての対象</option>
          {Object.entries(ENTITY_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select value={filters.action ?? ""} onChange={(e) => setFilter("action", e.target.value)}>
          <option value="">すべての操作</option>
          {Object.entries(ACTION_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select value={filters.source ?? ""} onChange={(e) => setFilter("source", e.target.value)}>
          <option value="">すべての発生元</option>
          {Object.entries(SOURCE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {filters.batch_id && (
          <button onClick={() => setFilter("batch_id", "")}>
            バッチの絞り込みを解除
          </button>
        )}
      </div>

      {error && <QueryError error={error} onRetry={() => refetch()} />}

      {isLoading ? (
        <p className="muted">読み込み中…</p>
      ) : (
        <>
          <p className="muted">{total.toLocaleString()} 件</p>
          <ChangeLogTable logs={data?.items ?? []} />

          {total > PAGE_SIZE && (
            <div className="actions">
              <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                ← 前
              </button>
              <span className="muted" style={{ alignSelf: "center" }}>
                {page + 1} / {lastPage + 1}
              </span>
              <button disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
                次 →
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
