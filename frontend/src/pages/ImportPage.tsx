import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, type ImportKeyField } from "../api/client";
import LinkSheetImport from "../components/LinkSheetImport";
import { saveBlob } from "../lib/download";
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

const KEY_OPTIONS: { value: ImportKeyField; label: string; hint: string }[] = [
  {
    value: "code",
    label: "管理ID（推奨）",
    hint: "社内台帳の番号で突合します。ホスト名を変えても同じ機器として更新できます",
  },
  {
    value: "hostname",
    label: "ホスト名",
    hint: "管理IDが無い台帳向け。ホスト名を変えると別機器として増えます",
  },
  {
    value: "serial_number",
    label: "シリアル番号",
    hint: "シリアルが入っている行だけ突合できます。空の行はエラーになります",
  },
];

/** だいたいこのサイズを超えたら「時間がかかる」と先に伝える（Excel は特に重い） */
const LARGE_FILE_BYTES = 20 * 1024 * 1024;

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

/**
 * 機器台帳の取り込み。
 *
 * 既定の突合キーは管理ID。プロジェクトで使っている番号があれば、それを
 * キーにする方がホスト名より安全で分かりやすい。書き出したファイルに
 * 「内部ID」列がある場合は、キーよりそちらが優先される。
 */
export default function ImportPage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [keyField, setKeyField] = useState<ImportKeyField>("code");
  const [updateExisting, setUpdateExisting] = useState(true);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ImportResult | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

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

  const downloadErrors = useMutation({
    mutationFn: async (encoding: "utf-8" | "shift_jis") => {
      const blob = await api.importErrorsCsv({
        file: file!,
        keyField,
        updateExisting,
        mapping: Object.keys(mapping).length ? mapping : undefined,
        commit: false,
        encoding,
      });
      saveBlob(blob, "netvision_import_errors.csv");
    },
  });

  useEffect(() => {
    if (!run.isPending && !downloadErrors.isPending) {
      setElapsedSec(0);
      return;
    }
    setElapsedSec(0);
    const started = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [run.isPending, downloadErrors.isPending]);

  const pickFile = (picked: File | null) => {
    setFile(picked);
    setMapping({});
    setResult(null);
  };

  /** キーや更新方針を変えたら、前の確認結果は使えない */
  const changeKeyField = (next: ImportKeyField) => {
    setKeyField(next);
    setResult(null);
  };
  const changeUpdateExisting = (next: boolean) => {
    setUpdateExisting(next);
    setResult(null);
  };

  // プレビュー結果に載っている列（マッピング済み + 未対応）が対応付けの編集対象
  const columns = result ? [...Object.keys(result.mapping), ...result.unmapped_columns] : [];
  const blocked = !result || result.errors > 0 || result.missing_required_fields.length > 0;
  const keyHint = KEY_OPTIONS.find((o) => o.value === keyField)?.hint;
  const busy = run.isPending || downloadErrors.isPending;
  const largeFile = Boolean(file && file.size >= LARGE_FILE_BYTES);
  const excelFile = Boolean(file && /\.xlsx?$/i.test(file.name));

  return (
    <>
      <h1>台帳の取り込み</h1>
      <p className="subtitle">
        既存の Excel / CSV 台帳をそのまま読み込みます。列名は自動で対応付け、
        合っていない列は下の「列の対応付け」で選び直せます。取り込み前に必ず内容を確認できます。
        <br />
        既存機器との突合は、まず「管理ID」を使うのがおすすめです。書き出したファイルに
        「内部ID」列がある場合は、それがいちばん確実です。
        <br />
        行数が多いとき（数万〜十数万）は CSV の方が速く、待ち時間も短くなります。
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
          {largeFile && (
            <p className="muted" style={{ margin: "6px 0 0", fontSize: "0.85rem" }}>
              大きなファイルです。確認・取り込みに数分かかることがあります
              {excelFile ? "（可能なら CSV にすると速いです）" : ""}。
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="key">既存機器の判定キー</label>
          <select
            id="key"
            value={keyField}
            onChange={(e) => changeKeyField(e.target.value as ImportKeyField)}
          >
            {KEY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {keyHint && (
            <p className="muted" style={{ margin: "6px 0 0", fontSize: "0.85rem" }}>
              {keyHint}
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="update">既存機器の扱い</label>
          <select
            id="update"
            value={updateExisting ? "update" : "skip"}
            onChange={(e) => changeUpdateExisting(e.target.value === "update")}
          >
            <option value="update">値が入っている列だけ更新する</option>
            <option value="skip">スキップする（新規のみ取り込む）</option>
          </select>
        </div>
        <div className="field">
          <label>テンプレート</label>
          <a href={api.importTemplateUrl}>記入用CSVをダウンロード</a>
          <p className="muted" style={{ margin: "6px 0 0", fontSize: "0.85rem" }}>
            管理ID列付きです。社内の番号を入れてから取り込むと、あとから直しやすいです。
          </p>
        </div>
      </div>

      <div className="actions">
        <button disabled={!file || busy} onClick={() => run.mutate(false)}>
          {run.isPending && run.variables === false ? "確認中…" : "内容を確認（取り込まない）"}
        </button>
        <button
          className="primary"
          disabled={blocked || busy}
          onClick={() => run.mutate(true)}
        >
          {run.isPending && run.variables === true ? "取り込み中…" : "取り込む"}
        </button>
      </div>

      {busy && (
        <p className="muted" style={{ marginTop: 12 }} role="status">
          処理中です（{formatElapsed(elapsedSec)}）。行数が多いと数分かかることがあります。
          画面を閉じず、そのまま待ってください。
        </p>
      )}

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
              {result.missing_required_fields.includes(keyField) && (
                <>
                  {" "}
                  判定キーに「{KEY_OPTIONS.find((o) => o.value === keyField)?.label}」を選んでいるので、
                  その列も必要です。
                </>
              )}
            </div>
          ) : result.errors > 0 ? (
            <div className="error" style={{ marginTop: 16 }}>
              <div>
                エラーが {result.errors} 行あります。1行でもエラーがあると取り込みは実行されません。
              </div>
              <div style={{ marginTop: 8 }}>
                エラー行だけを書き出して Excel で修正し、そのファイルを取り込み直せます。
              </div>
              <div className="actions">
                <button
                  disabled={downloadErrors.isPending}
                  onClick={() => downloadErrors.mutate("utf-8")}
                >
                  エラー行をCSVで書き出す
                </button>
                <button
                  disabled={downloadErrors.isPending}
                  onClick={() => downloadErrors.mutate("shift_jis")}
                >
                  Shift_JIS で書き出す
                </button>
              </div>
            </div>
          ) : null}

          {downloadErrors.error && (
            <div className="error">{(downloadErrors.error as Error).message}</div>
          )}

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
            変えたあとは「対応付けを反映して再確認」を押してください。
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
                          {f.required || f.name === keyField ? " *" : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="actions">
            <button disabled={busy} onClick={() => run.mutate(false)}>
              対応付けを反映して再確認
            </button>
          </div>

          <h2>行ごとの結果</h2>
          {result.rows_total > result.rows.length && (
            <p className="muted" style={{ marginTop: -6 }}>
              {result.rows_total} 行中 {result.rows.length}{" "}
              行を表示しています（エラー行を優先）。件数の内訳は上の合計が正です。
              {result.errors > 0 && " エラー行の全件は CSV で書き出せます。"}
            </p>
          )}
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

      <hr />
      <LinkSheetImport />
    </>
  );
}
