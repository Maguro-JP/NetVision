import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import type { SiteImportResult } from "../api/types";

/**
 * 拠点台帳の取り込み。
 *
 * 拠点は数が多い。住所・状態・開設日は機器の台帳には入っていないので、
 * 拠点だけの表でまとめて入れられないと、画面から1つずつ直すことになる。
 * 2万を超える拠点ではそれは無理がある。
 *
 * 書き出した表をそのまま直して戻せる並びにしてある。
 */
export default function SiteSheetImport() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [result, setResult] = useState<SiteImportResult | null>(null);

  const send = useMutation({
    mutationFn: ({ target, commit }: { target: File; commit: boolean }) =>
      api.importSites(target, commit),
    onSuccess: (data) => {
      setResult(data);
      if (data.committed) {
        for (const key of ["sites", "devices", "summary"]) {
          queryClient.invalidateQueries({ queryKey: [key] });
        }
      }
    },
  });

  const take = (list: FileList | null) => {
    const picked = Array.from(list ?? [])[0];
    if (picked) {
      setFile(picked);
      setResult(null);
      send.mutate({ target: picked, commit: false });
    }
  };

  return (
    <section className="link-import">
      <h2>拠点台帳から取り込む</h2>
      <p className="subtitle">
        住所・状態・開設日・閉鎖日をまとめて入れられます。突き合わせの鍵は拠点IDで、
        IDが無い行は拠点名で探し、それも無ければ新しく登録します。
        拠点に紐づく機器は触りません。
      </p>

      <div className="toolbar">
        <a href={api.siteExportUrl("xlsx")}>
          <button>いまの拠点を書き出す（Excel）</button>
        </a>
        <a href={api.siteExportUrl("csv")}>
          <button>いまの拠点を書き出す（CSV）</button>
        </a>
      </div>

      <div
        className={`dropzone${over ? " over" : ""}${send.isPending ? " busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          take(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          hidden
          accept=".csv,.tsv,.xlsx"
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
          }}
        />
        {send.isPending ? (
          <span>読んでいます…</span>
        ) : (
          <span>
            書き込んだ拠点台帳をここに<strong>ドラッグ＆ドロップ</strong>
            <br />
            <span className="muted">
              クリックしてファイルを選ぶこともできます
            </span>
          </span>
        )}
      </div>

      {send.error && (
        <div className="error">{(send.error as Error).message}</div>
      )}

      {result && (
        <div className="drop-result">
          <strong>{file?.name}</strong>
          <span className="muted"> {result.total_rows} 行</span>
          <ul>
            <li>
              新しく登録する拠点: <strong>{result.created}</strong>
            </li>
            <li>
              直す拠点: <strong>{result.updated}</strong>
              {result.unchanged > 0 && (
                <span className="muted">
                  （変わらない行 {result.unchanged}）
                </span>
              )}
            </li>
            {result.unmapped_columns.length > 0 && (
              <li className="muted">
                読まなかった列: {result.unmapped_columns.join(" / ")}
              </li>
            )}
          </ul>

          {result.issues.length > 0 && (
            <>
              <p className="muted">直してほしい行:</p>
              <ul className="issues">
                {result.issues.map((issue, index) => (
                  <li key={index}>
                    {issue.row_number > 0 ? `${issue.row_number} 行目: ` : ""}
                    {issue.message}
                  </li>
                ))}
              </ul>
            </>
          )}

          {result.committed ? (
            <p className="ok">取り込みました。</p>
          ) : (
            <button
              className="primary"
              disabled={!file || send.isPending}
              onClick={() =>
                file && send.mutate({ target: file, commit: true })
              }
            >
              この内容で取り込む
            </button>
          )}
        </div>
      )}
    </section>
  );
}
