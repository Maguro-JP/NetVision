import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import type { LinkImportResult } from "../api/types";

/**
 * 結線表の取り込み。
 *
 * 導入のとき、手元にあるのが結線表だけということがある。機器を登録し終える
 * 前でも書けるように、ひな形を出して、書いた分を取り込めるようにする。
 *
 * 台帳にまだ無い機器の行は預かっておき、その機器が登録された時点で結線に
 * 変わる。取り込み直しは要らない。
 */
export default function LinkSheetImport() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [result, setResult] = useState<LinkImportResult | null>(null);

  const send = useMutation({
    mutationFn: ({ target, commit }: { target: File; commit: boolean }) =>
      api.importLinks(target, commit),
    onSuccess: (data) => {
      setResult(data);
      if (data.committed) {
        for (const key of ["topology", "topologyStats", "devices"]) {
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
      <h2>結線表から取り込む</h2>
      <p className="subtitle">
        Config が揃う前でも、結線表があれば図を立ち上げられます。機器はホスト名でも
        管理IDでも書けます（台帳にあるどちらかに合っていれば繋がります）。
        台帳にまだ無い機器の行は預かっておき、その機器を登録した時点で自動的に結線になります
        （取り込み直しは要りません）。
      </p>

      <div className="toolbar">
        <a href={api.linkTemplateUrl}>
          <button>記入用のひな形をダウンロード（Excel）</button>
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
            書き込んだ結線表をここに<strong>ドラッグ＆ドロップ</strong>
            <br />
            <span className="muted">クリックしてファイルを選ぶこともできます</span>
          </span>
        )}
      </div>

      {send.error && <div className="error">{(send.error as Error).message}</div>}

      {result && (
        <div className="drop-result">
          <strong>{file?.name}</strong>
          <span className="muted"> {result.total_rows} 行</span>
          <ul>
            <li>
              結線になる行: <strong>{result.linked}</strong>
              {result.already > 0 && <span className="muted">（既にある行 {result.already}）</span>}
            </li>
            {result.pending > 0 && (
              <li>
                機器がまだ無い行: <strong>{result.pending}</strong>
                <span className="muted">
                  {" "}
                  — 預かります。その機器を登録した時点で結線になります
                </span>
              </li>
            )}
            {result.unmapped_columns.length > 0 && (
              <li className="muted">読まなかった列: {result.unmapped_columns.join(" / ")}</li>
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
              onClick={() => file && send.mutate({ target: file, commit: true })}
            >
              この内容で取り込む
            </button>
          )}
        </div>
      )}
    </section>
  );
}
