import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type {
  ProductImportResult,
  ReferenceSourceKind,
  VulnerabilityImportResult,
} from "../api/types";
import { formatDate } from "../lib/format";

const SOURCE_LABEL: Record<ReferenceSourceKind, string> = {
  manual: "手入力",
  csv: "CSV取り込み",
  nvd: "NIST NVD",
  kev: "CISA KEV",
  vendor: "ベンダー資料",
};

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ReferencePage() {
  const queryClient = useQueryClient();
  const [productFile, setProductFile] = useState<File | null>(null);
  const [cveFile, setCveFile] = useState<File | null>(null);
  const [fetchedAt, setFetchedAt] = useState(today());
  const [staleOnly, setStaleOnly] = useState(false);
  const [q, setQ] = useState("");
  const [productResult, setProductResult] = useState<ProductImportResult | null>(null);
  const [cveResult, setCveResult] = useState<VulnerabilityImportResult | null>(null);

  const { data: freshness } = useQuery({
    queryKey: ["referenceFreshness"],
    queryFn: api.referenceFreshness,
  });
  const { data: products } = useQuery({
    queryKey: ["products", q, staleOnly],
    queryFn: () => api.listProducts({ q: q || undefined, stale_only: staleOnly ? "true" : undefined }),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["products"] });
    queryClient.invalidateQueries({ queryKey: ["referenceFreshness"] });
    queryClient.invalidateQueries({ queryKey: ["devices"] });
    queryClient.invalidateQueries({ queryKey: ["summary"] });
    queryClient.invalidateQueries({ queryKey: ["insights"] });
    queryClient.invalidateQueries({ queryKey: ["vulnerabilities"] });
  };

  const importProducts = useMutation({
    mutationFn: () => api.importProducts(productFile!, fetchedAt),
    onSuccess: (data) => {
      setProductResult(data);
      refresh();
    },
  });

  const importCve = useMutation({
    mutationFn: () => api.importVulnerabilities(cveFile!, fetchedAt),
    onSuccess: (data) => {
      setCveResult(data);
      refresh();
    },
  });

  const removeProduct = useMutation({
    mutationFn: (id: number) => api.deleteProduct(id),
    onSuccess: refresh,
  });

  return (
    <>
      <h1>製品情報</h1>
      <p className="subtitle">
        型番ごとの EoL 情報と脆弱性情報をここで管理します（機器の仕様書やコマンド
        リファレンスは <Link to="/documents">メーカー資料</Link> です）。EoL は機器ごとではなく
        <strong>型番に紐づけて</strong>いるので、1回直せば該当機器すべてに反映されます。
        <br />
        閉域環境を前提に、<strong>オフラインでの持ち込み</strong>を主な経路にしています。
        外部への通信は行いません。
      </p>

      {freshness && (
        <>
          <h2>リファレンスの鮮度</h2>
          <p className="muted" style={{ marginTop: -6 }}>
            自動更新はされません。{freshness.stale_after_days}日より古い情報は「要更新」として表示します。
          </p>
          <div className="cards">
            <div className="card">
              <div className="label">登録型番</div>
              <div className="value">{freshness.product_count}</div>
            </div>
            <div className="card">
              <div className="label">要更新の型番</div>
              <div
                className="value"
                style={{ color: freshness.products_stale ? "var(--sev-warning)" : undefined }}
              >
                {freshness.products_stale}
              </div>
            </div>
            <div className="card">
              <div className="label">リファレンス有りの機器</div>
              <div className="value">
                {freshness.devices_with_product_reference}
                <span className="muted" style={{ fontSize: 14 }}>
                  {" "}
                  / {freshness.devices_total}
                </span>
              </div>
            </div>
            <div className="card">
              <div className="label">登録CVE</div>
              <div className="value">{freshness.vulnerability_count}</div>
            </div>
            <div className="card">
              <div className="label">最も古い取得日</div>
              <div className="value" style={{ fontSize: 18 }}>
                {freshness.oldest_product_fetch
                  ? formatDate(freshness.oldest_product_fetch)
                  : "—"}
              </div>
            </div>
          </div>

          {freshness.devices_without_reference.length > 0 && (
            <>
              <p className="muted" style={{ marginTop: 14 }}>
                リファレンスが未登録の型番（この機器は EoL を判定できません）:
              </p>
              <div className="toolbar">
                {freshness.devices_without_reference.map((name) => (
                  <span key={name} className="badge unknown">
                    {name}
                  </span>
                ))}
              </div>
            </>
          )}
        </>
      )}

      <h2>データの取り込み</h2>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="fetched">情報の取得日（この日時点の情報として記録します）</label>
          <input
            id="fetched"
            type="date"
            value={fetchedAt}
            onChange={(e) => setFetchedAt(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="products">型番リファレンス（CSV / Excel）</label>
          <input
            id="products"
            type="file"
            accept=".csv,.tsv,.xlsx"
            onChange={(e) => {
              setProductFile(e.target.files?.[0] ?? null);
              setProductResult(null);
            }}
          />
          <a href={api.productTemplateUrl}>記入用テンプレートをダウンロード</a>
        </div>
        <div className="field">
          <label htmlFor="cve">脆弱性データ（JSON）</label>
          <input
            id="cve"
            type="file"
            accept=".json"
            onChange={(e) => {
              setCveFile(e.target.files?.[0] ?? null);
              setCveResult(null);
            }}
          />
          <span className="muted">NVD 2.0 / CISA KEV / 簡易形式（自動判別）</span>
        </div>
      </div>

      <div className="actions">
        <button
          className="primary"
          disabled={!productFile || importProducts.isPending}
          onClick={() => importProducts.mutate()}
        >
          型番リファレンスを取り込む
        </button>
        <button
          disabled={!cveFile || importCve.isPending}
          onClick={() => importCve.mutate()}
        >
          脆弱性データを取り込む
        </button>
      </div>

      {(importProducts.error || importCve.error) && (
        <div className="error">
          {((importProducts.error ?? importCve.error) as Error).message}
        </div>
      )}

      {productResult && (
        <div
          className="card"
          style={{
            marginTop: 12,
            borderColor: productResult.errors ? "var(--sev-expired)" : "var(--sev-ok)",
          }}
        >
          {productResult.missing_required_fields.length > 0 ? (
            <>必須列（ベンダー / 型番）が見つかりません。テンプレートを確認してください。</>
          ) : (
            <>
              型番リファレンス: 新規 {productResult.created} / 更新 {productResult.updated} / 変更なし{" "}
              {productResult.unchanged} / エラー {productResult.errors}
            </>
          )}
          {productResult.rows
            .filter((r) => r.action === "error")
            .map((r) => (
              <div key={r.row_number} style={{ color: "var(--sev-expired)", marginTop: 6 }}>
                {r.row_number}行目 {r.vendor} {r.model}: {r.message}
              </div>
            ))}
        </div>
      )}

      {cveResult && (
        <div className="card" style={{ marginTop: 12, borderColor: "var(--sev-ok)" }}>
          {cveResult.format.toUpperCase()} 形式として読み込みました。新規 {cveResult.created} /
          更新 {cveResult.updated} / 影響範囲 {cveResult.affects_created} 件
          {cveResult.messages.map((message) => (
            <div key={message} className="muted" style={{ marginTop: 6 }}>
              {message}
            </div>
          ))}
        </div>
      )}

      <h2>型番リファレンス</h2>
      <div className="toolbar">
        <input
          placeholder="ベンダー / 型番 / 製品名で検索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 260 }}
        />
        <label className="muted" style={{ alignSelf: "center" }}>
          <input
            type="checkbox"
            checked={staleOnly}
            onChange={(e) => setStaleOnly(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          要更新のみ
        </label>
      </div>

      {(products ?? []).length === 0 ? (
        <div className="empty">型番リファレンスが登録されていません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ベンダー / 型番</th>
              <th>販売終了</th>
              <th>サポート終了</th>
              <th>推奨後継機</th>
              <th>該当機器</th>
              <th>出典</th>
              <th>取得日</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(products ?? []).map((product) => (
              <tr key={product.id}>
                <td>
                  <strong>{product.vendor}</strong> {product.model}
                  {product.product_name && (
                    <div className="muted">{product.product_name}</div>
                  )}
                </td>
                <td>{formatDate(product.eos_date)}</td>
                <td>{formatDate(product.eol_date)}</td>
                <td className="muted">{product.recommended_replacement ?? "—"}</td>
                <td>{product.device_count}</td>
                <td className="muted">
                  {SOURCE_LABEL[product.source]}
                  {product.source_url && (
                    <>
                      {" "}
                      <a href={product.source_url} target="_blank" rel="noreferrer">
                        原典
                      </a>
                    </>
                  )}
                </td>
                <td>
                  {formatDate(product.fetched_at)}
                  {product.stale && (
                    <div>
                      <span className="badge warning">要更新</span>
                    </div>
                  )}
                </td>
                <td>
                  <button className="danger" onClick={() => removeProduct.mutate(product.id)}>
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
