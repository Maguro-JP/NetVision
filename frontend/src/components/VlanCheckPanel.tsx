import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { VlanFinding } from "../api/types";
import { LEVEL_LABEL } from "./L3Panel";

/**
 * VLAN の名前が、機器と台帳でそろっているか。
 *
 * 番号でつながるので、名前が食い違っても通信は動く。困るのは人の側で、
 * 「10 番を止めて」が別の機器では別の意味になる。1台ずつ見ても分からない。
 */

/** 重いものから先に。下まで読まないと大事な指摘に気づかない、を避ける。 */
export function sortVlanFindings(findings: VlanFinding[]): VlanFinding[] {
  const weight = { high: 0, medium: 1, low: 2 };
  return [...findings].sort(
    (a, b) => weight[a.level] - weight[b.level] || a.vlan_id - b.vlan_id,
  );
}

export default function VlanCheckPanel() {
  const { data } = useQuery({
    queryKey: ["vlanCheck"],
    queryFn: api.vlanCheck,
  });

  if (!data) return null;

  return (
    <>
      <h2>VLAN の名前がそろっているか</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        番号でつながるので、名前が違っても通信は動きます。困るのは人の側で、
        「10 番を止めて」が別の機器では別の意味になります。
      </p>
      {data.vlans_seen > 0 && (
        <p className="muted">
          {data.devices_with_vlans} 台に入っている {data.vlans_seen} 個の番号を見ました
        </p>
      )}

      {data.notes.map((note) => (
        <div className="empty" key={note}>
          {note}
        </div>
      ))}

      {data.findings.length === 0 && data.vlans_seen > 0 && (
        <div className="empty">名前の食い違いはありませんでした。</div>
      )}

      {data.findings.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 60 }}>度合い</th>
              <th style={{ width: 80 }}>番号</th>
              <th>内容</th>
              <th>なぜ困るか</th>
            </tr>
          </thead>
          <tbody>
            {sortVlanFindings(data.findings).map((finding) => (
              <tr key={`${finding.vlan_id}-${finding.title}`}>
                <td>{LEVEL_LABEL[finding.level]}</td>
                <td>VLAN {finding.vlan_id}</td>
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
