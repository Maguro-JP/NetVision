import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { AskResult } from "../api/types";

/**
 * やりたいことを文章で書いて、設計を起こす。
 *
 * 頼む側の頭の中には「本社の A の下に B を足したい」という文がある。それを
 * フォームの項目に分解できるのは台帳を知っている人だけで、知らない人は
 * そこで止まる。
 *
 * 読み取った内容は必ず出す。読み違えたまま進まれるより、直せるほうが早い。
 */

const EXAMPLES = [
  "本社の core-sw-01 の下に floor3-sw-01 を増設したい",
  "core-sw-01 の下にスイッチを1台足したい",
  "大阪支社の osaka-sw-01 の下に wifi-sw-01 を増設（無線用）",
];

/** 起こせたのか、まだ足りないのかを一言で。表を見なくても分かるように。 */
export function statusOf(result: AskResult | null): string {
  if (!result) return "";
  if (result.plan) return "設計を起こしました";
  if (result.upstream_choices.length > 0)
    return "どの機器のことか選んでください";
  return "もう少し情報が要ります";
}

export default function AskPage() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);

  const ask = useMutation({
    mutationFn: (body: Parameters<typeof api.askAddition>[0]) =>
      api.askAddition(body),
    onSuccess: setResult,
  });

  const plan = result?.plan ?? null;

  return (
    <>
      <h1>ことばで依頼</h1>
      <p className="subtitle">
        やりたいことを、そのまま書いてください。台帳にある機器の名前が入っていれば、
        そこから設計を起こします。読み取った内容も出すので、違っていれば直せます。
      </p>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        style={{ width: "100%" }}
        placeholder="例: 本社の core-sw-01 の下に floor3-sw-01 を増設したい"
      />
      <div className="actions">
        <button
          className="primary"
          disabled={!text.trim() || ask.isPending}
          onClick={() => ask.mutate({ text })}
        >
          {ask.isPending ? "読み取り中…" : "設計を起こす"}
        </button>
      </div>
      <p className="muted">
        書き方の例:
        {EXAMPLES.map((example) => (
          <button
            key={example}
            style={{ marginLeft: 8 }}
            onClick={() => setText(example)}
          >
            {example}
          </button>
        ))}
      </p>
      {ask.error && <div className="error">{(ask.error as Error).message}</div>}

      {result && (
        <>
          <h2>{statusOf(result)}</h2>

          <table>
            <tbody>
              <tr>
                <th style={{ width: 160 }}>つなぐ先</th>
                <td>
                  {result.reading.upstream_hostname ?? "読み取れませんでした"}
                </td>
              </tr>
              <tr>
                <th>足す機器</th>
                <td>{result.reading.new_hostname ?? "読み取れませんでした"}</td>
              </tr>
              <tr>
                <th>拠点</th>
                <td>{result.reading.site ?? "指定なし"}</td>
              </tr>
              <tr>
                <th>用途</th>
                <td>{result.reading.purpose || "指定なし"}</td>
              </tr>
            </tbody>
          </table>
          {result.reading.notes.length > 0 && (
            <ul className="muted">
              {result.reading.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}

          {result.questions.length > 0 && (
            <div className="error">
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {result.questions.map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            </div>
          )}

          {result.upstream_choices.length > 0 && (
            <div className="actions" style={{ flexWrap: "wrap" }}>
              {result.upstream_choices.map((choice) => (
                <button
                  key={choice.device_id}
                  onClick={() =>
                    ask.mutate({ text, upstream_device_id: choice.device_id })
                  }
                >
                  {choice.hostname}
                  {choice.site ? `（${choice.site}）` : ""}
                </button>
              ))}
            </div>
          )}

          {plan && (
            <>
              <h2>決めたこと</h2>
              <table>
                <thead>
                  <tr>
                    <th>項目</th>
                    <th>決めた値</th>
                    <th>なぜそうしたか</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.decisions.map((decision) => (
                    <tr key={decision.topic}>
                      <td>{decision.topic}</td>
                      <td>{decision.choice}</td>
                      <td>
                        {decision.why}
                        {decision.evidence.length > 0 && (
                          <div className="muted" style={{ marginTop: 4 }}>
                            根拠: {decision.evidence.join(" / ")}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {plan.warnings.length > 0 && (
                <div className="error" style={{ marginTop: 12 }}>
                  <ul style={{ margin: 0, paddingLeft: 20 }}>
                    {plan.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              <h2>{plan.upstream_hostname} に足す分</h2>
              <pre className="config-preview">{plan.upstream_config}</pre>
              <ul className="muted">
                {plan.upstream_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>

              <h2>{plan.hostname}（新規）</h2>
              <pre className="config-preview">{plan.device_config}</pre>
              <ul className="muted">
                {plan.device_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>

              <h2>投入の前後で確かめること</h2>
              <ul>
                {plan.checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>
              <p className="muted">
                ここで作るのは下書きです。実機には投入しません。言葉が分からないときは
                <Link to="/glossary"> 用語集</Link>を見てください。
              </p>
            </>
          )}
        </>
      )}
    </>
  );
}
