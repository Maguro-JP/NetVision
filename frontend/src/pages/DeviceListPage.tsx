import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import DeviceTable from "../components/DeviceTable";
import { DEVICE_STATUS_LABEL, DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";

export default function DeviceListPage() {
  const [params, setParams] = useSearchParams();

  const filters = {
    q: params.get("q") ?? undefined,
    device_type: params.get("device_type") ?? undefined,
    status: params.get("status") ?? undefined,
    severity: params.get("severity") ?? undefined,
    site_id: params.get("site_id") ? Number(params.get("site_id")) : undefined,
  };

  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: api.listSites });
  const { data, isLoading, error } = useQuery({
    queryKey: ["devices", filters],
    queryFn: () => api.listDevices(filters),
  });

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <>
      <h1>機器一覧</h1>
      <p className="subtitle">型番・バージョン・導入時期・保守期限をまとめて管理します。</p>

      <div className="toolbar">
        <input
          placeholder="ホスト名 / 型番 / シリアル で検索"
          defaultValue={filters.q ?? ""}
          onChange={(e) => setFilter("q", e.target.value)}
          style={{ minWidth: 260 }}
        />
        <select value={filters.device_type ?? ""} onChange={(e) => setFilter("device_type", e.target.value)}>
          <option value="">すべての種別</option>
          {Object.entries(DEVICE_TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select value={filters.status ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
          <option value="">すべての状態</option>
          {Object.entries(DEVICE_STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select value={filters.severity ?? ""} onChange={(e) => setFilter("severity", e.target.value)}>
          <option value="">すべてのアラート</option>
          {Object.entries(SEVERITY_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={filters.site_id?.toString() ?? ""}
          onChange={(e) => setFilter("site_id", e.target.value)}
        >
          <option value="">すべての拠点</option>
          {(sites ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <Link to="/devices/new">
          <button className="primary">機器を追加</button>
        </Link>
      </div>

      <div className="toolbar">
        <span className="muted" style={{ alignSelf: "center" }}>
          絞り込んだ {data?.length ?? 0} 件を書き出す:
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

      {error && <div className="error">{(error as Error).message}</div>}
      {isLoading ? <p className="muted">読み込み中…</p> : <DeviceTable devices={data ?? []} />}
    </>
  );
}
