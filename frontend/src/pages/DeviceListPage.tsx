import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { ColumnChoiceFilter, ColumnTextFilter } from "../components/ColumnFilter";
import DeviceTable, { type DeviceColumn } from "../components/DeviceTable";
import QueryError from "../components/QueryError";
import WindowLink from "../components/WindowLink";
import { SEARCH_DELAY_MS, useDebouncedValue } from "../lib/debounce";
import { DEVICE_STATUS_LABEL, DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";

/**
 * 機器一覧。
 *
 * 絞り込みは列見出しの下に置く。表計算と同じ場所にあれば、どの列が絞れるか
 * 見ただけで分かるし、上に並べた選択肢と列の対応を目で探さずに済む。
 * 上のバーには、全体を探す欄と追加だけを残す。
 *
 * 列のフィルターは Excel と同じく、その場で文字を打って絞れる。
 * 決まっている候補（種別・運用・状態）は、打つと候補が減る。
 */
/** 1ページに出す件数。多すぎると描画が重く、少なすぎるとページ送りが増える。 */
const PAGE_SIZE = 100;

/** URL に載せる列フィルター。空なら載せない。 */
const TEXT_KEYS = ["hostname", "product", "os_version", "maintenance", "eol"] as const;

type TextKey = (typeof TEXT_KEYS)[number];

export default function DeviceListPage() {
  const [params, setParams] = useSearchParams();

  const urlQ = params.get("q") ?? "";
  const [qInput, setQInput] = useState(urlQ);
  const qDebounced = useDebouncedValue(qInput, SEARCH_DELAY_MS);

  // 打ち終わってから URL に載せる。打っている最中に戻すと、次の文字が消える
  useEffect(() => {
    if (qInput !== qDebounced) return;
    if (urlQ !== qInput) setQInput(urlQ);
  }, [urlQ, qInput, qDebounced]);

  useEffect(() => {
    if ((qDebounced || "") === urlQ) return;
    const next = new URLSearchParams(params);
    if (qDebounced) next.set("q", qDebounced);
    else next.delete("q");
    next.delete("page");
    setParams(next, { replace: true });
  }, [qDebounced, urlQ, params, setParams]);

  const onlyIncomplete = params.get("incomplete") === "1";
  const page = Math.max(0, Number(params.get("page") ?? 0));

  const textFilters = Object.fromEntries(
    TEXT_KEYS.map((key) => [key, params.get(key) || undefined]),
  ) as Partial<Record<TextKey, string>>;

  const filters = {
    q: qDebounced || undefined,
    hostname: textFilters.hostname,
    product: textFilters.product,
    os_version: textFilters.os_version,
    maintenance: textFilters.maintenance,
    eol: textFilters.eol,
    device_type: params.get("device_type") ?? undefined,
    status: params.get("status") ?? undefined,
    severity: params.get("severity") ?? undefined,
    site_id: params.get("site_id") ? Number(params.get("site_id")) : undefined,
    incomplete: onlyIncomplete || undefined,
  };

  // 出力の窓へ、いまの絞り込みをそのまま渡す。画面と出したものを揃える
  const exportQuery = (() => {
    const next = new URLSearchParams({ kind: "devices" });
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") next.set(key, String(value));
    }
    return `?${next.toString()}`;
  })();

  const { data: sites } = useQuery({
    queryKey: ["sites"],
    queryFn: api.listSites,
  });
  // 全件は取りに行かない。16万台の環境では1ページ分でも全件読み込むと
  // 10秒かかっていた（絞り込みも件数も、いまはサーバー側で済ませている）
  const { data, isLoading, isFetching, error, refetch } = useQuery({
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

  const setFilter = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      // 条件を変えたら先頭のページへ戻す。3ページ目のまま絞り込むと
      // 「該当なし」に見えてしまう
      next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const setTextFilter = useCallback(
    (key: TextKey, value: string) => setFilter(key, value),
    [setFilter],
  );

  const statusChoices = useMemo(
    () => [
      { value: "incomplete", label: "情報不足" },
      ...Object.entries(SEVERITY_LABEL).map(([value, label]) => ({ value, label })),
    ],
    [],
  );
  const typeChoices = useMemo(
    () => Object.entries(DEVICE_TYPE_LABEL).map(([value, label]) => ({ value, label })),
    [],
  );
  const operationChoices = useMemo(
    () => Object.entries(DEVICE_STATUS_LABEL).map(([value, label]) => ({ value, label })),
    [],
  );
  const siteChoices = useMemo(
    () => (sites ?? []).map((s) => ({ value: String(s.id), label: s.name })),
    [sites],
  );

  const statusValue = onlyIncomplete ? "incomplete" : (params.get("severity") ?? "");

  const columnFilters: Partial<Record<DeviceColumn, React.ReactNode>> = {
    status: (
      <ColumnChoiceFilter
        value={statusValue}
        choices={statusChoices}
        placeholder="保守を探す…"
        title="保守・EoL の状態で絞り込む。文字を打つと候補が減ります"
        onChange={(next) => {
          const updated = new URLSearchParams(params);
          updated.delete("severity");
          updated.delete("incomplete");
          if (next === "incomplete") updated.set("incomplete", "1");
          else if (next) updated.set("severity", next);
          updated.delete("page");
          setParams(updated, { replace: true });
        }}
      />
    ),
    hostname: (
      <ColumnTextFilter
        value={params.get("hostname") ?? ""}
        placeholder="管理ID・ホスト名…"
        title="ホスト名に含む文字で絞り込む"
        onChange={(next) => setTextFilter("hostname", next)}
      />
    ),
    type: (
      <ColumnChoiceFilter
        value={params.get("device_type") ?? ""}
        choices={typeChoices}
        placeholder="種別を探す…"
        title="種別で絞り込む。文字を打つと候補が減ります"
        onChange={(next) => setFilter("device_type", next)}
      />
    ),
    operation: (
      <ColumnChoiceFilter
        value={params.get("status") ?? ""}
        choices={operationChoices}
        placeholder="運用を探す…"
        title="運用状態で絞り込む。文字を打つと候補が減ります"
        onChange={(next) => setFilter("status", next)}
      />
    ),
    product: (
      <ColumnTextFilter
        value={params.get("product") ?? ""}
        placeholder="メーカー / 型番…"
        title="メーカーまたは型番に含む文字で絞り込む"
        onChange={(next) => setTextFilter("product", next)}
      />
    ),
    site: (
      <ColumnChoiceFilter
        value={params.get("site_id") ?? ""}
        choices={siteChoices}
        placeholder="拠点を探す…"
        title="拠点で絞り込む。文字を打つと候補が減ります"
        onChange={(next) => setFilter("site_id", next)}
      />
    ),
    version: (
      <ColumnTextFilter
        value={params.get("os_version") ?? ""}
        placeholder="バージョン…"
        title="OSバージョンに含む文字で絞り込む"
        onChange={(next) => setTextFilter("os_version", next)}
      />
    ),
    maintenance: (
      <ColumnTextFilter
        value={params.get("maintenance") ?? ""}
        placeholder="年や日付…"
        title="保守期限に含む文字で絞り込む（例: 2026）"
        onChange={(next) => setTextFilter("maintenance", next)}
      />
    ),
    eol: (
      <ColumnTextFilter
        value={params.get("eol") ?? ""}
        placeholder="年や日付…"
        title="EoLに含む文字で絞り込む（例: 2027-03）"
        onChange={(next) => setTextFilter("eol", next)}
      />
    ),
  };

  return (
    <>
      <h1>機器一覧</h1>
      <p className="subtitle">型番・バージョン・導入時期・保守期限をまとめて管理します。</p>

      <div className="toolbar">
        <input
          type="search"
          placeholder="どの項目でも探す（ホスト名・IP・拠点・種別の呼び名など）"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          style={{ minWidth: 320, flex: 1 }}
          title="メーカー・型番・シリアル・管理IP・拠点・メモ・種別の呼び名など、どの項目でも部分一致で探します"
        />
        {isFetching && !isLoading && <span className="muted">探しています…</span>}
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

      {error && <QueryError error={error} onRetry={() => refetch()} />}
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
        {/* 出力は1つにまとめる。Excel か CSV か、文字コードをどうするかは
            別ウィンドウで中身を見ながら選ぶ */}
        <WindowLink
          className="button"
          to={`/export${exportQuery}`}
          name="netvision-export"
          width={1200}
          height={900}
          title="絞り込んだ機器を、中身を見てから Excel / CSV で保存します"
        >
          絞り込んだ {total.toLocaleString()} 件を出力する
        </WindowLink>
      </div>
    </>
  );
}
