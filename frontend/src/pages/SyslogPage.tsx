import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { LogReport } from "../api/types";

/**
 * 機器のログを読んで、何が起きたかを出す。
 *
 * Config は「どう設定されているか」で、ログは「実際に何が起きたか」。
 * 設定は正しいのに落ちている、片系で動き続けている、は片方だけでは分からない。
 *
 * 1行ずつ眺めるためのものではない。同じことが何度起きたか、どの機器で起きたか、
 * いつからいつまでか。そこが分かると当たりが付く。
 */

/** 読めた割合を一言で。全部読めたと思われないように。 */
export function coverageOf(report: LogReport): string {
  if (report.lines === 0) return "";
  if (report.unreadable === 0) return `${report.lines} 行すべてを読みました`;
  return `${report.lines} 行のうち ${report.parsed} 行を読みました（${report.unreadable} 行は読めていません）`;
}

/** 期間を1行で。いつからいつまでの話なのかが分からないと、判断できない。 */
export function periodOf(report: LogReport): string {
  if (!report.first_at || !report.last_at) return "時刻が読み取れませんでした";
  const from = new Date(report.first_at).toLocaleString("ja-JP");
  const to = new Date(report.last_at).toLocaleString("ja-JP");
  return from === to ? from : `${from} 〜 ${to}`;
}

export default function SyslogPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [text, setText] = useState("");
  const [report, setReport] = useState<LogReport | null>(null);

  const drop = useMutation({
    mutationFn: (files: File[]) => api.readSyslogFiles(files),
    onSuccess: setReport,
  });
  const paste = useMutation({
    mutationFn: () => api.readSyslog(text),
    onSuccess: setReport,
  });

  const take = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length) drop.mutate(files);
  };

  return (
    <>
      <h1>ログを読む</h1>
      <p className="subtitle">
        機器が出したログを放り込むか貼り付けてください。同じことが何度起きたか、どの機器で
        起きたか、いつからいつまでかを出します。台帳には何も書きません。
      </p>

      <div className="config-drop">
        <div
          className={`dropzone${over ? " over" : ""}${drop.isPending ? " busy" : ""}`}
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
            multiple
            hidden
            onChange={(e) => {
              take(e.target.files);
              e.target.value = "";
            }}
          />
          {drop.isPending ? (
            <span>読んでいます…</span>
          ) : (
            <span>
              ここにログを放り込む（何個でも）
              <br />
              <small className="muted">
                複数のファイルは、つなげて1つとして数えます。Shift_JIS
                でも読めます
              </small>
            </span>
          )}
        </div>
      </div>
      {drop.error && (
        <div className="error">{(drop.error as Error).message}</div>
      )}

      <h2>貼り付けて読む</h2>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        spellCheck={false}
        style={{ width: "100%", fontFamily: "monospace" }}
        placeholder="Aug 22 10:00:01 core-sw-01 %LINK-3-UPDOWN: ..."
      />
      <div className="actions">
        <button
          className="primary"
          disabled={!text.trim() || paste.isPending}
          onClick={() => paste.mutate()}
        >
          {paste.isPending ? "読んでいます…" : "貼り付けたものを読む"}
        </button>
        {report && <button onClick={() => setReport(null)}>結果を消す</button>}
      </div>
      {paste.error && (
        <div className="error">{(paste.error as Error).message}</div>
      )}

      {report && (
        <>
          <h2>読んだ結果</h2>
          <div className="cards">
            <div className="card">
              <div className="label">読めた行</div>
              <div className="value">{report.parsed}</div>
            </div>
            {Object.entries(report.by_severity).map(([label, count]) => (
              <div className="card" key={label}>
                <div className="label">{label}</div>
                <div className="value">{count}</div>
              </div>
            ))}
          </div>
          <p className="muted">
            {periodOf(report)} — {coverageOf(report)}
          </p>
          {report.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}

          {report.patterns.length > 0 && (
            <>
              <h3>何が起きたか</h3>
              <table>
                <thead>
                  <tr>
                    <th>度合い</th>
                    <th>回数</th>
                    <th>内容</th>
                    <th>どの機器で</th>
                  </tr>
                </thead>
                <tbody>
                  {report.patterns.map((pattern) => (
                    <tr key={pattern.code}>
                      <td>{pattern.severity_label}</td>
                      <td>{pattern.count}</td>
                      <td>
                        {pattern.meaning || pattern.code}
                        <div className="muted" style={{ marginTop: 4 }}>
                          {pattern.code}
                          {pattern.sample &&
                            ` — ${pattern.sample.slice(0, 120)}`}
                        </div>
                      </td>
                      <td>{pattern.hosts.join(" / ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {report.by_host.length > 0 && (
            <>
              <h3>機器ごとの件数</h3>
              <table>
                <thead>
                  <tr>
                    <th>機器</th>
                    <th>件数</th>
                    <th>重いもの</th>
                    <th>台帳</th>
                  </tr>
                </thead>
                <tbody>
                  {report.by_host.map((row) => (
                    <tr key={row.host}>
                      <td>{row.host}</td>
                      <td>{row.count}</td>
                      <td>{row.heavy}</td>
                      <td>
                        {report.known_hosts.includes(row.host) ? (
                          <Link
                            to={`/devices?q=${encodeURIComponent(row.host)}`}
                          >
                            台帳にあります
                          </Link>
                        ) : (
                          <span className="muted">見当たりません</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <p className="muted">
            言葉が分からないときは<Link to="/glossary"> 言葉の説明</Link>
            を見てください。
          </p>
        </>
      )}
    </>
  );
}
