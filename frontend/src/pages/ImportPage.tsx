import { useMutation, useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { ImportResult, ImportRowAction } from "../api/types";

const ACTION_LABEL: Record<ImportRowAction, string> = {
  create: "新規",
  update: "更新",
  skip: "スキップ",
  error: "エラー",
};

const ACTION_COLOR: Record<ImportRowAction, string> = {
  create: "var(--sev-ok)",
  update: "var(--accent)",
  skip: "var(--sev-unknown)",
  error: "var(--sev-expired)",
};

export default function ImportPage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [keyField, setKeyField] = useState<"hostname" | "serial_number">("hostname");
  const [updateExisting, setUpdateExisting] = useState(true);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ImportResult | null>(null);

  const { data: fields } = useQuery({ queryKey: ["importFields"], queryFn: api.importFields });

  const run = useMutation({
    mutationFn: (commit: boolean) =>
      api.importDevices({
        file: file!,
        keyField,
        updateExisting,
        mapping: Object.keys(mapping).length ? mapping : undefined,
        commit,
      }),
    onSuccess: (data) => {
      setResult(data);
      if (data.committed) {
        queryClient.invalidateQueries({ queryKey: ["devices"] });
        queryClient.invalidateQueries({ queryKey: ["sites"] });
        queryClient.invalidateQueries({ queryKey: ["summary"] });
      }
    },
  });

  const pickFile = (picked: File | null) => {
    setFile(picked);
    setMapping({});
    setResult(null);
  };

  // プレビュー結果に載っている列（マッピング済み + 未対応）が対応付けの編集対象
  const columns = result ? [...Object.keys(result.mapping), ...result.unmapped_columns] : [];
  const blocked = !result || result.errors > 0 || result.missing_required_fields.length > 0;

  return (
    <>
      <h1>台帳の取り込み</h1>
      <p className="subtitle">
        既存の Excel / CSV 台帳をそのまま読み込みます。列名は自動で対応付け、
        合っていない列は下の「列の対応付け」で選び直せます。取り込み前に必ず内容を確認できます。
        <br />
        <Link to="/devices">機器一覧</Link>
        から書き出したファイルは、そのまま編集して取り込み直せます。
      </p>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="file">ファイル（.csv / .tsv / .xlsx）</label>
          <input
            id="file"
            type="file"
            accept=".csv,.tsv,.xlsx,.xlsm"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="field">
          <label htmlFor="key">既存機器の判定キー</label>
          <select
            id="key"
            value={keyField}
            onChange={(e) => setKeyField(e.target.value as "hostname" | "serial_number")}
          >
            <option value="hostname">ホスト名</option>
            <option value="serial_number">シリアル番号</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="update">既存機器の扱い</label>
          <select
            id="update"
            value={updateExisting ? "update" : "skip"}
            onChange={(e) => setUpdateExisting(e.target.value === "update")}
          >
            <option value="update">値が入っている列だけ更新する</option>
            <option value="skip">スキップする（新規のみ取り込む）</option>
          </select>
        </div>
        <div className="field">
          <label>テンプレート</label>
          <a href={api.importTemplateUrl}>記入用CSVをダウンロード</a>
        </div>
      </div>

      <div className="actions">
        <button disabled={!file || run.isPending} onClick={() => run.mutate(false)}>
          {run.isPending ? "確認中…" : "内容を確認（取り込まない）"}
        </button>
        <button
          className="primary"
          disabled={blocked || run.isPending}
          onClick={() => run.mutate(true)}
        >
          取り込む
        </button>
      </div>

      {run.error && <div className="error">{(run.error as Error).message}</div>}

      {result && (
        <>
          {result.committed ? (
            <div className="card" style={{ marginTop: 16, borderColor: "var(--sev-ok)" }}>
              取り込みが完了しました。新規 {result.created} 件 / 更新 {result.updated} 件 / 保守契約{" "}
              {result.contracts_added} 件を登録しました。
              <Link to="/devices"> 機器一覧を見る →</Link>
            </div>
          ) : result.missing_required_fields.length > 0 ? (
            <div className="error" style={{ marginTop: 16 }}>
              必須項目に対応する列が見つかりません:{" "}
              {result.missing_required_fields
                .map((f) => fields?.find((x) => x.name === f)?.label ?? f)
                .join(" / ")}
              。下の「列の対応付け」で指定するか、ヘッダ行を確認してください。
            </div>
          ) : result.errors > 0 ? (
            <div className="error" style={{ marginTop: 16 }}>
              エラーが {result.errors} 行あります。1行でもエラーがあると取り込みは実行されません。
            </div>
          ) : null}

          <h2>取り込み内容</h2>
          <div className="cards">
            <div className="card">
              <div className="label">対象行</div>
              <div className="value">{result.total_rows}</div>
            </div>
            <div className="card">
              <div className="label">新規</div>
              <div className="value" style={{ color: "var(--sev-ok)" }}>
                {result.created}
              </div>
            </div>
            <div className="card">
              <div className="label">更新</div>
              <div className="value" style={{ color: "var(--accent)" }}>
                {result.updated}
              </div>
            </div>
            <div className="card">
              <div className="label">スキップ</div>
              <div className="value">{result.skipped}</div>
            </div>
            <div className="card">
              <div className="label">エラー</div>
              <div className="value" style={{ color: "var(--sev-expired)" }}>
                {result.errors}
              </div>
            </div>
            <div className="card">
              <div className="label">保守契約</div>
              <div className="value">{result.contracts_added}</div>
            </div>
          </div>

          <h2>列の対応付け</h2>
          <p className="muted" style={{ marginTop: -6 }}>
            自動判別を変えたい列はここで指定してください。「（取り込まない）」を選ぶとその列は無視します。
          </p>
          <table>
            <thead>
              <tr>
                <th>ファイルの列名</th>
                <th>データの例</th>
                <th>取り込み先</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((column) => (
                <tr key={column}>
                  <td>{column}</td>
                  <td className="muted" style={{ maxWidth: 360, overflow: "hidden" }}>
                    {(result.column_samples[column] ?? []).join(" / ") || "（空）"}
                  </td>
                  <td>
                    <select
                      value={mapping[column] ?? result.mapping[column] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, [column]: e.target.value })}
                    >
                      <option value="">（取り込まない）</option>
                      {(fields ?? []).map((f) => (
                        <option key={f.name} value={f.name}>
                          {f.label}
                          {f.required ? " *" : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="actions">
            <button disabled={run.isPending} onClick={() => run.mutate(false)}>
              対応付けを反映して再確認
            </button>
          </div>

          <h2>行ごとの結果</h2>
          {result.rows.length === 0 ? (
            <div className="empty">確認できる行がありません</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>行</th>
                  <th>キー</th>
                  <th>処理</th>
                  <th>保守契約</th>
                  <th>メッセージ</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => (
                  <tr key={row.row_number}>
                    <td>{row.row_number}</td>
                    <td>{row.key || "—"}</td>
                    <td style={{ color: ACTION_COLOR[row.action], fontWeight: 600 }}>
                      {ACTION_LABEL[row.action]}
                    </td>
                    <td>{row.contract_added ? "追加" : "—"}</td>
                    <td style={{ whiteSpace: "normal" }}>
                      {row.issues.length === 0
                        ? "—"
                        : row.issues
                            .map((i) =>
                              i.column
                                ? `${fields?.find((f) => f.name === i.column)?.label ?? i.column}: ${i.message}`
                                : i.message,
                            )
                            .join(" / ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
