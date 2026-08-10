import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import DeviceTable, { type DeviceColumn } from "../components/DeviceTable";
import { DEVICE_STATUS_LABEL, DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";

/**
 * 機器一覧。
 *
 * 絞り込みは列見出しの下に置く。表計算と同じ場所にあれば、どの列が絞れるか
 * 見ただけで分かるし、上に並べた選択肢と列の対応を目で探さずに済む。
 * 上のバーには、探す欄と追加だけを残す。
 */
/** 1ページに出す件数。多すぎると描画が重く、少なすぎるとページ送りが増える。 */
const PAGE_SIZE = 100;

export default function DeviceListPage() {
  const [params, setParams] = useSearchParams();

  // 情報不足だけを見たいときがある。取り込んだ直後の埋め作業がこれ
  const onlyIncomplete = params.get("incomplete") === "1";
  const page = Math.max(0, Number(params.get("page") ?? 0));

  const filters = {
    q: params.get("q") ?? undefined,
    device_type: params.get("device_type") ?? undefined,
    status: params.get("status") ?? undefined,
    severity: params.get("severity") ?? undefined,
    site_id: params.get("site_id") ? Number(params.get("site_id")) : undefined,
    incomplete: onlyIncomplete || undefined,
  };

  const { data: sites } = useQuery({
    queryKey: ["sites"],
    queryFn: api.listSites,
  });
  // 全件は取りに行かない。16万台の環境では1ページ分でも全件読み込むと
  // 10秒かかっていた（絞り込みも件数も、いまはサーバ側で済ませている）
  const { data, isLoading, error } = useQuery({
    queryKey: ["devices", filters, page],
    queryFn: () => api.listDevicePage({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    placeholderData: (previous) => previous,
  });

  const shown = data?.items ?? [];
  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  const goToPage = (next: number) => {
    const params2 = new URLSearchParams(params);
    if (next <= 0) params2.delete("page");
    else params2.set("page", String(next));
    setParams(params2, { replace: true });
  };

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    // 条件を変えたら先頭のページへ戻す。3ページ目のまま絞り込むと
    // 「該当なし」に見えてしまう
    next.delete("page");
    setParams(next, { replace: true });
  };

  const dropdown = (key: string, all: string, options: Record<string, string>) => (
    <select value={params.get(key) ?? ""} onChange={(e) => setFilter(key, e.target.value)}>
      <option value="">{all}</option>
      {Object.entries(options).map(([value, label]) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </select>
  );

  const columnFilters: Partial<Record<DeviceColumn, React.ReactNode>> = {
    status: (
      <select
        value={onlyIncomplete ? "incomplete" : (params.get("severity") ?? "")}
        onChange={(e) => {
          const next = new URLSearchParams(params);
          next.delete("severity");
          next.delete("incomplete");
          if (e.target.value === "incomplete") next.set("incomplete", "1");
          else if (e.target.value) next.set("severity", e.target.value);
          next.delete("page");
          setParams(next, { replace: true });
        }}
      >
        <option value="">すべて</option>
        <option value="incomplete">情報不足</option>
        {Object.entries(SEVERITY_LABEL).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    ),
    type: dropdown("device_type", "すべて", DEVICE_TYPE_LABEL),
    site: (
      <select
        value={filters.site_id?.toString() ?? ""}
        onChange={(e) => setFilter("site_id", e.target.value)}
      >
        <option value="">すべて</option>
        {(sites ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    ),
    operation: dropdown("status", "すべて", DEVICE_STATUS_LABEL),
  };

  return (
    <>
      <h1>機器一覧</h1>
      <p className="subtitle">型番・バージョン・導入時期・保守期限をまとめて管理します。</p>

      <div className="toolbar">
        <input
          placeholder="ホスト名 / 型番 / シリアル で探す"
          defaultValue={filters.q ?? ""}
          onChange={(e) => setFilter("q", e.target.value)}
          style={{ minWidth: 260 }}
        />
        {!onlyIncomplete && (
          <button
            className="link-button"
            onClick={() => setFilter("incomplete", "1")}
            title="Config から起こした機器は、読み取れなかった項目が空のままです"
          >
            情報不足だけ見る
          </button>
        )}
        <span className="toolbar-spacer" />
        <Link to="/devices/new">
          <button className="primary">機器を追加</button>
        </Link>
      </div>

      {error && <div className="error">{(error as Error).message}</div>}
      {isLoading ? (
        <p className="muted">読み込み中…</p>
      ) : (
        <>
          <DeviceTable devices={shown} columnFilters={columnFilters} />
          <div className="pager">
            <button disabled={page <= 0} onClick={() => goToPage(page - 1)}>
              ← 前
            </button>
            <span className="muted">
              {total === 0
                ? "0 件"
                : `${total.toLocaleString()} 件中 ${(page * PAGE_SIZE + 1).toLocaleString()}〜${Math.min(total, (page + 1) * PAGE_SIZE).toLocaleString()} 件`}
            </span>
            <button disabled={page >= lastPage} onClick={() => goToPage(page + 1)}>
              次 →
            </button>
          </div>
        </>
      )}

      <div className="toolbar">
        <span className="muted" style={{ alignSelf: "center" }}>
          絞り込んだ {total.toLocaleString()} 件を書き出す:
        </span>
        <a href={api.exportUrl("xlsx", filters)}>
          <button>Excel (.xlsx)</button>
        </a>
        <a href={api.exportUrl("csv", filters, { encoding: "utf-8" })}>
          <button>CSV (UTF-8)</button>
        </a>
        <a href={api.exportUrl("csv", filters, { encoding: "shift_jis" })}>
          <button>CSV (Shift_JIS)</button>
        </a>
        <a
          href={api.exportUrl("csv", filters, { includeStatus: false })}
          title="算出列を除いた、取り込み用の列だけのCSVです。編集して取り込み直せます。"
        >
          <button>取り込み用CSV</button>
        </a>
      </div>
    </>
  );
}
