import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { AskBackAnswer, ConfigCheck, L3Finding } from "../api/types";
import ConfigEditor from "../components/ConfigEditor";
import { LEVEL_LABEL, sortFindings, sourcesLine } from "../components/L3Panel";

/**
 * Config を放り込んで、その場で見てもらう。
 *
 * 台帳に入れる前に「どこか変じゃないか」を知りたい場面のほうが多い。
 * 試験用に書いたもの、業者から届いたもの、自分で書いてみたもの。
 *
 * 台帳には何も書かない。見るだけのつもりが機器を増やしていた、が
 * いちばん困る。
 */

/** どうやって答えたかの呼び名。答えの確からしさが変わるので、必ず出す。 */
export const SOURCE_LABEL: Record<string, string> = {
  glossary: "用語集から",
  model: "手元のモデルが",
  prompt: "答えられません",
};

/** 見た結果を1行で。何個も放り込んだときに、開く前に当たりを付けられる。 */
export function summaryOf(check: ConfigCheck): string {
  const heavy = check.findings.filter((f) => f.level === "high").length;
  if (heavy > 0) return `急ぎの指摘が ${heavy} 件`;
  if (check.findings.length > 0) return `指摘が ${check.findings.length} 件`;
  if (check.differences.length > 0)
    return `この現場のやり方と違うところが ${check.differences.length} 件`;
  return "気になるところはありませんでした";
}

function Findings({ items, title }: { items: L3Finding[]; title: string }) {
  if (items.length === 0) return null;
  return (
    <>
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th style={{ width: 60 }}>度合い</th>
            <th>内容</th>
            <th>なぜそう言えるか</th>
          </tr>
        </thead>
        <tbody>
          {sortFindings(items).map((finding) => (
            <tr key={finding.title}>
              <td>{LEVEL_LABEL[finding.level]}</td>
              <td>{finding.title}</td>
              <td>
                {finding.why}
                {finding.evidence.length > 0 && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    見たところ: {finding.evidence.join(" / ")}
                  </div>
                )}
                {sourcesLine(finding.sources) && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    {sourcesLine(finding.sources)}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function ConfigCheckPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [text, setText] = useState("");
  const [results, setResults] = useState<ConfigCheck[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskBackAnswer | null>(null);

  const drop = useMutation({
    mutationFn: (files: File[]) => api.checkConfigFiles(files),
    onSuccess: (data) => setResults((current) => [...data, ...current]),
  });
  const paste = useMutation({
    mutationFn: () => api.checkConfigText(text),
    onSuccess: (data) => setResults((current) => [data, ...current]),
  });

  const askBack = useMutation({
    mutationFn: () => api.askBack(question, text || ""),
    onSuccess: setAnswer,
  });

  const take = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length) drop.mutate(files);
  };

  return (
    <>
      <h1>Config の点検</h1>
      <p className="subtitle">
        ファイルを放り込むか、貼り付けてください。読み取って、気になるところを出します。
        台帳には何も書きません。取り込みたいときは
        <Link to="/configs"> Config の取り込み</Link>から。
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
              ここに Config を放り込む（何個でも）
              <br />
              <small className="muted">
                running-config / show 出力。Shift_JIS でも読めます
              </small>
            </span>
          )}
        </div>
      </div>
      {drop.error && (
        <div className="error">{(drop.error as Error).message}</div>
      )}

      <h2>貼り付けて点検する</h2>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        style={{ width: "100%" }}
        placeholder="hostname ..."
      />
      <div className="actions">
        <button
          className="primary"
          disabled={!text.trim() || paste.isPending}
          onClick={() => paste.mutate()}
        >
          {paste.isPending ? "点検しています…" : "点検する"}
        </button>
        {results.length > 0 && (
          <button onClick={() => setResults([])}>結果を消す</button>
        )}
      </div>
      {paste.error && (
        <div className="error">{(paste.error as Error).message}</div>
      )}

      {results.map((check, index) => (
        <div
          key={`${check.filename}-${index}`}
          className="card"
          style={{ marginTop: 16 }}
        >
          <h2 style={{ marginTop: 0 }}>
            {check.filename}
            <span className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
              {summaryOf(check)}
            </span>
          </h2>
          <p className="muted">
            {check.vendor_label} /{" "}
            {check.hostname ?? "ホスト名は読めませんでした"}
            {check.model ? ` / ${check.model}` : ""}
            {check.os_version ? ` / ${check.os_version}` : ""}
            {" — "}
            ポート {check.interfaces} / VLAN {check.vlans} / 経路 {check.routes}
          </p>

          <Findings items={check.findings} title="気になるところ" />
          <Findings
            items={check.differences}
            title="この現場のやり方と違うところ"
          />

          {check.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}
        </div>
      ))}

      {text.trim() && <ConfigEditor body={text} />}

      <h2>結果について聞く</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        出した結果について聞き返せます。言葉の意味なら、その場で答えます。それ以外は
        手元のモデルが答えます。モデルが無いときは、会社で認められている AI
        へ貼るための 依頼文を出します。
      </p>
      <div className="toolbar">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="例: トランクって何ですか / なぜこの経路は使われないのですか"
          style={{ minWidth: 420 }}
        />
        <button
          disabled={!question.trim() || askBack.isPending}
          onClick={() => askBack.mutate()}
        >
          {askBack.isPending ? "聞いています…" : "聞く"}
        </button>
      </div>
      {askBack.error && (
        <div className="error">{(askBack.error as Error).message}</div>
      )}

      {answer && (
        <div className="card">
          <div className="muted">
            {SOURCE_LABEL[answer.source] ?? answer.source}
            {answer.used.length > 0 && ` — ${answer.used.join(" / ")}`}
          </div>
          <p style={{ whiteSpace: "pre-wrap" }}>{answer.answer}</p>
          {answer.prompt && (
            <>
              <p className="muted">この依頼文を貼って聞いてください。</p>
              <pre className="config-preview">{answer.prompt}</pre>
            </>
          )}
          {answer.note && <p className="muted">{answer.note}</p>}
        </div>
      )}

      <p className="muted" style={{ marginTop: 24 }}>
        言葉が分からないときは<Link to="/glossary"> 用語集</Link>
        を見てください。
      </p>
    </>
  );
}
