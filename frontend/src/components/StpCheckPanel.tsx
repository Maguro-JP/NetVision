import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { StpFinding } from "../api/types";
import { LEVEL_LABEL } from "./L3Panel";

/**
 * 木の根（STP のルート）が、どこに立つか。
 *
 * 優先度は相手と比べてはじめて意味を持つので、1台の Config を見ても
 * 根の場所は言えない。ここは機器をまたいで、そこだけを見る。
 */

/** 重いものから先に。下まで読まないと大事な指摘に気づかない、を避ける。 */
export function sortStpFindings(findings: StpFinding[]): StpFinding[] {
  const weight = { high: 0, medium: 1, low: 2 };
  return [...findings].sort(
    (a, b) => weight[a.level] - weight[b.level] || a.title.localeCompare(b.title),
  );
}

/** VLAN 番号の順に。全体に効く書き方（all）は最後に置く。 */
export function sortRoots(roots: Record<string, string>): [string, string][] {
  return Object.entries(roots).sort(([a], [b]) => {
    if (a === "all") return 1;
    if (b === "all") return -1;
    return Number(a) - Number(b);
  });
}

export default function StpCheckPanel() {
  const { data } = useQuery({ queryKey: ["stpCheck"], queryFn: api.stpCheck });

  if (!data) return null;

  const roots = sortRoots(data.roots);

  return (
    <>
      <h2>ループを止める木の根がどこにあるか</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        優先度は相手と比べてはじめて意味を持つので、1台の設定を見ても根の場所は
        分かりません。書いていなければ、根は MAC アドレスの若い機器に立ちます。
      </p>
      {data.devices_seen > 0 && (
        <p className="muted">{data.devices_seen} 台の設定を見ました</p>
      )}

      {data.notes.map((note) => (
        <div className="empty" key={note}>
          {note}
        </div>
      ))}

      {roots.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 120 }}>範囲</th>
              <th>根になる機器</th>
            </tr>
          </thead>
          <tbody>
            {roots.map(([vlan, host]) => (
              <tr key={vlan}>
                <td>{vlan === "all" ? "全体" : `VLAN ${vlan}`}</td>
                <td>{host}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data.findings.length === 0 && data.devices_seen > 0 && (
        <div className="empty">根の置き方で気になるところはありませんでした。</div>
      )}

      {data.findings.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 60 }}>度合い</th>
              <th>内容</th>
              <th>なぜ困るか</th>
            </tr>
          </thead>
          <tbody>
            {sortStpFindings(data.findings).map((finding) => (
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
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
