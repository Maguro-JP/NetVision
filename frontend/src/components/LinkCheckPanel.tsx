import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { LinkFinding } from "../api/types";
import { LEVEL_LABEL, sourcesLine } from "./L3Panel";

/**
 * 線の両端で、設定がそろっているか。
 *
 * 1台ずつの Config を見ても分からない壊れ方がある。片側だけトランク、片側だけ
 * 大きい MTU、片側だけ速度を固定。どれも片方の機器の中では正しいので、
 * 1台ずつ見ている限り見つからない。しかも通信が止まらないので長く残る。
 */

/** 見た本数と、見られなかった本数を一言で。全部見たと思われないように。 */
export function coverageOf(seen: number, withConfig: number): string {
  if (seen === 0) return "";
  if (seen === withConfig) return `${seen} 本すべての両端を見ました`;
  return `${seen} 本のうち ${withConfig} 本の両端を見ました（残りはポートの設定が読めていません）`;
}

/** 重いものから先に。下まで読まないと大事な指摘に気づかない、を避ける。 */
export function sortLinkFindings(findings: LinkFinding[]): LinkFinding[] {
  const weight = { high: 0, medium: 1, low: 2 };
  return [...findings].sort((a, b) => weight[a.level] - weight[b.level]);
}

export default function LinkCheckPanel() {
  const { data } = useQuery({
    queryKey: ["linkCheck"],
    queryFn: api.linkCheck,
  });

  if (!data) return null;

  return (
    <>
      <h2>線の両端がそろっているか</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        片側だけトランク、片側だけ大きい
        MTU、片側だけ速度を固定。どれも片方の機器の中では
        正しいので、1台ずつ見ていると見つかりません。通信も止まらないので長く残ります。
      </p>
      {data.links_seen > 0 && (
        <p className="muted">
          {coverageOf(data.links_seen, data.links_with_config)}
        </p>
      )}

      {data.notes.map((note) => (
        <div className="empty" key={note}>
          {note}
        </div>
      ))}

      {data.findings.length === 0 && data.links_with_config > 0 && (
        <div className="empty">
          見た範囲では、両端の食い違いはありませんでした。
        </div>
      )}

      {data.findings.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 60 }}>度合い</th>
              <th>どの線か</th>
              <th>内容</th>
              <th>なぜ困るか</th>
            </tr>
          </thead>
          <tbody>
            {sortLinkFindings(data.findings).map((finding) => (
              <tr key={`${finding.link_id}-${finding.title}`}>
                <td>{LEVEL_LABEL[finding.level]}</td>
                <td>
                  {finding.a_hostname} {finding.a_interface}
                  <div className="muted">
                    — {finding.b_hostname} {finding.b_interface}
                  </div>
                </td>
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
      )}
    </>
  );
}
