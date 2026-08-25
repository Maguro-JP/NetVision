import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { PipelineStage } from "../api/types";

/**
 * 人 → AI → ロジック → AI → ロジック → AI → 人。
 *
 * AI に全部やらせると、決まりきったところまで毎回書き直され、そのたびに
 * 間違える余地が増える。ロジックだけにすると、言い回しの揺れを受け止められず、
 * 出てきたものの意味も伝わらない。
 *
 * 得意なところだけを交互に使い、どの段が何をしたかをそのまま見せる。
 * どこで決まった値なのかが分からないと、出てきたものを確かめようがない。
 */

/** 段ごとの見出し。誰の仕事かが一目で分かるようにする。 */
export function labelOf(stage: PipelineStage): string {
  if (stage.kind === "human") return "人";
  if (stage.kind === "ai") return stage.skipped ? "AI（使っていません）" : "AI";
  return "ロジック";
}

/** その段の一言。飛ばしたときは理由を出す。黙ると、やったように見える。 */
export function detailOf(stage: PipelineStage): string {
  if (stage.skipped) return `${stage.summary}（${stage.skipped}）`;
  if (stage.model) return `${stage.summary}（${stage.model}）`;
  return stage.summary;
}

export default function PipelinePage() {
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [request, setRequest] = useState("");

  const { data: devices } = useQuery({
    queryKey: ["devices", "pipeline"],
    queryFn: () => api.listDevices({ limit: 200 }),
  });

  const run = useMutation({
    mutationFn: () => api.runPipeline(deviceId as number, { request }),
  });
  const result = run.data;

  return (
    <>
      <h1>処理の流れ</h1>
      <p className="subtitle">
        やりたいことを書くと、AI とロジックが交互に受け持って下書きを作ります。
        値を決めるのはロジックだけで、AI
        は言い直しと、足りない行を書くところだけを
        受け持ちます。どの段が何をしたかは、下にそのまま出します。
      </p>

      <div className="card">
        <label>
          機器
          <select
            value={deviceId ?? ""}
            onChange={(event) =>
              setDeviceId(
                event.target.value ? Number(event.target.value) : null,
              )
            }
          >
            <option value="">選んでください</option>
            {(devices ?? []).map((device) => (
              <option key={device.id} value={device.id}>
                {device.hostname}
              </option>
            ))}
          </select>
        </label>
        <label>
          やりたいこと
          <textarea
            rows={3}
            value={request}
            onChange={(event) => setRequest(event.target.value)}
            placeholder="例: GigabitEthernet1/0/5 を VLAN 20 のアクセスポートにして"
          />
        </label>
        <div className="actions">
          <button
            className="primary"
            disabled={!deviceId || !request.trim() || run.isPending}
            onClick={() => run.mutate()}
          >
            {run.isPending ? "通しています…" : "通す"}
          </button>
        </div>
      </div>

      {result && (
        <>
          <section>
            <h2>処理の経路</h2>
            <ol>
              {result.stages.map((stage, index) => (
                <li key={`${stage.name}-${index}`}>
                  {stage.name}（{labelOf(stage)}）— {detailOf(stage)}
                </li>
              ))}
            </ol>
          </section>

          {result.explanation && (
            <section>
              <h2>各段のはたらき</h2>
              <p style={{ whiteSpace: "pre-wrap" }}>{result.explanation}</p>
            </section>
          )}

          {result.decided.length > 0 && (
            <section>
              <h2>決めたこと</h2>
              <ul>
                {result.decided.map((item) => (
                  <li key={item.key}>
                    {item.key}: {item.value}
                    <div className="muted">{item.why}</div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {result.gaps.length > 0 && (
            <section>
              <h2>決められなかったこと</h2>
              <ul>
                {result.gaps.map((gap) => (
                  <li key={gap.key}>
                    {gap.question}
                    <div className="muted">{gap.why}</div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h2>下書き</h2>
            <pre>{result.body || "（行はありません）"}</pre>
          </section>

          {(result.risks.length > 0 ||
            result.findings.length > 0 ||
            result.ungrounded.length > 0) && (
            <section>
              <h2>入れる前に見るところ</h2>
              <ul>
                {result.risks.map((risk) => (
                  <li key={risk}>{risk}</li>
                ))}
                {result.findings.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
                {result.ungrounded.length > 0 && (
                  <li>
                    資料に裏付けが無い行が {result.ungrounded.length} 行あります
                  </li>
                )}
              </ul>
            </section>
          )}

          {result.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}
        </>
      )}
    </>
  );
}
