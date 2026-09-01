import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { LEVEL_LABEL } from "./L3Panel";
import { sortStpFindings } from "./StpCheckPanel";

/**
 * 2台で1台に見せる仕組み（HA / failover / VSX / MLAG）が、組として成り立っているか。
 *
 * 片方にしか入っていない、両方とも primary。どれも1台の中では正しいので、
 * 機器をまたがないと見つからない。普段は片方で動くので、切り替わる日まで気づかない。
 */
export default function HaCheckPanel() {
  const { data } = useQuery({ queryKey: ["haCheck"], queryFn: api.haCheck });

  if (!data) return null;

  const groups = Object.entries(data.groups);

  return (
    <>
      <h2>2台で1台に見せる組がそろっているか</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        HA / failover / VSX / MLAG は、両方の機器に入ってはじめて組になります。
        片方だけでも普段は動くので、切り替わる日まで気づきません。
      </p>
      {data.devices_seen > 0 && (
        <p className="muted">{data.devices_seen} 台に入っていました</p>
      )}

      {data.notes.map((note) => (
        <div className="empty" key={note}>
          {note}
        </div>
      ))}

      {groups.length > 0 && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 240 }}>組</th>
              <th>機器</th>
            </tr>
          </thead>
          <tbody>
            {groups.map(([key, hosts]) => (
              <tr key={key}>
                <td>{key}</td>
                <td>{hosts.join(" / ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data.findings.length === 0 && data.devices_seen > 0 && (
        <div className="empty">組として気になるところはありませんでした。</div>
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
