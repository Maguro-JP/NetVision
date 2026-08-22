import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { LinkCandidate } from "../api/types";

/**
 * 経路の情報から見つけた、線の候補。
 *
 * 図は LLDP / CDP か結線表から作るが、現場から出てくるのは running-config
 * だけということが多い。それでも L3 の情報は必ず書いてあるので、そこから
 * 引ける線を出す。
 *
 * 「たぶんこう」を勝手に台帳へ書かない。図は判断に使われるので、確かでない
 * 線が混ざると害のほうが大きい。候補として出し、選んだものだけ線にする。
 */

export const CONFIDENCE_LABEL: Record<LinkCandidate["confidence"], string> = {
  high: "確か",
  medium: "たぶん",
};

/** 言い切れるものだけ数える。「まとめて入れる」で何本入るかを先に見せるため。 */
export function sureCount(candidates: LinkCandidate[] | undefined): number {
  return (candidates ?? []).filter((c) => c.confidence === "high").length;
}

export default function LinkCandidates() {
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<string[]>([]);

  const { data: candidates } = useQuery({
    queryKey: ["linkCandidates"],
    queryFn: api.linkCandidates,
  });

  const done = () => {
    setPicked([]);
    queryClient.invalidateQueries({ queryKey: ["linkCandidates"] });
    queryClient.invalidateQueries({ queryKey: ["topology"] });
    queryClient.invalidateQueries({ queryKey: ["summary"] });
  };

  const applySure = useMutation({
    mutationFn: () => api.applyLinkCandidates(),
    onSuccess: done,
  });
  const applyPicked = useMutation({
    mutationFn: () =>
      api.applyLinkCandidates(
        picked.map((key) => key.split("-").map(Number) as [number, number]),
      ),
    onSuccess: done,
  });

  if (!candidates || candidates.length === 0) return null;

  const sure = sureCount(candidates);

  return (
    <>
      <h2>経路から結線を推定する</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        結線表も LLDP も無くても、同じセグメントにいる2台は Config
        から分かります。見つけた組を候補として出します。
        図に入れるかどうかは選んでください。
      </p>
      <div className="actions">
        <button
          className="primary"
          disabled={sure === 0 || applySure.isPending}
          onClick={() => applySure.mutate()}
        >
          確かなものだけ入れる（{sure} 本）
        </button>
        <button
          disabled={picked.length === 0 || applyPicked.isPending}
          onClick={() => applyPicked.mutate()}
        >
          選んだものを入れる（{picked.length} 本）
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>確からしさ</th>
            <th>片方</th>
            <th>もう片方</th>
            <th>セグメント</th>
            <th>なぜそう言えるか</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => {
            const key = `${candidate.a_device_id}-${candidate.b_device_id}`;
            return (
              <tr key={key}>
                <td>
                  <input
                    type="checkbox"
                    checked={picked.includes(key)}
                    onChange={() =>
                      setPicked((current) =>
                        current.includes(key)
                          ? current.filter((item) => item !== key)
                          : [...current, key],
                      )
                    }
                  />
                </td>
                <td>{CONFIDENCE_LABEL[candidate.confidence]}</td>
                <td>
                  {candidate.a_hostname}
                  <div className="muted">{candidate.a_interface}</div>
                </td>
                <td>
                  {candidate.b_hostname}
                  <div className="muted">{candidate.b_interface}</div>
                </td>
                <td>{candidate.segment}</td>
                <td>
                  {candidate.why}
                  <div className="muted" style={{ marginTop: 4 }}>
                    見たところ: {candidate.evidence.join(" / ")}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
