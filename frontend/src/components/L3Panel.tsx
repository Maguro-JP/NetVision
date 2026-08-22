import { useQuery } from "@tanstack/react-query";

import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { L3Finding } from "../api/types";

/**
 * 機器の L3 と、気になるところ。
 *
 * 経路の設定は、間違っていても半分は動く。だから気づくのが遅れて、障害の
 * 当日に初めて分かる。読める範囲で先に挙げておく。
 *
 * 指摘には必ず理由と根拠を出す。理由の無い指摘は直しようが無いうえ、
 * 直す必要の無いものまで直させてしまう。
 */

export const LEVEL_LABEL: Record<L3Finding["level"], string> = {
  high: "急ぎ",
  medium: "確認",
  low: "参考",
};

/** 重いものから先に見せる。下まで読まないと大事なものに気づかない、を避ける。 */
export function sortFindings(findings: L3Finding[]): L3Finding[] {
  const weight = { high: 0, medium: 1, low: 2 };
  return [...findings].sort((a, b) => weight[a.level] - weight[b.level]);
}

export default function L3Panel({ deviceId }: { deviceId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["deviceL3", deviceId],
    queryFn: () => api.deviceL3(deviceId),
  });

  if (isLoading) return null;
  if (!data) return null;

  if (!data.has_config) {
    return (
      <>
        <h2>経路と機能</h2>
        <div className="empty">
          この機器の Config がまだ取り込まれていません。running-config
          を取り込むと、
          経路・OSPF・BGP・ゲートウェイ冗長を読み取って、気になるところも挙げます。
        </div>
      </>
    );
  }

  const findings = sortFindings(data.findings);
  const nothing =
    data.routes.length === 0 &&
    !data.ospf &&
    !data.bgp &&
    data.redundancy.length === 0 &&
    data.features.length === 0;

  return (
    <>
      <h2>経路と機能</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        言葉が分からないときは
        <Link to="/glossary"> 言葉の説明</Link>
        を見てください。「切り替わらない」のように、困っていることからも探せます。
      </p>

      {findings.length > 0 && (
        <>
          <h3>気になるところ</h3>
          <table>
            <thead>
              <tr>
                <th>度合い</th>
                <th>内容</th>
                <th>なぜ気になるか</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((finding) => (
                <tr key={finding.title}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {LEVEL_LABEL[finding.level]}
                  </td>
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
        </>
      )}

      {nothing && (
        <div className="empty">
          この Config からは経路の設定が読み取れませんでした。L2
          だけの機器なら、これで正しい状態です。
        </div>
      )}

      {data.features.length > 0 && (
        <>
          <h3>使っている機能</h3>
          <p className="muted" style={{ marginTop: -8 }}>
            入っているかどうかと、主な値まで見ています。中身（ACL
            の1行ずつなど）は 解釈していません。
          </p>
          <table>
            <thead>
              <tr>
                <th>区分</th>
                <th>機能</th>
                <th>件数</th>
                <th>主な値</th>
              </tr>
            </thead>
            <tbody>
              {data.features.map((feature) => (
                <tr key={`${feature.category}-${feature.name}`}>
                  <td>{feature.category_label}</td>
                  <td>{feature.name}</td>
                  <td>{feature.count}</td>
                  <td>
                    {feature.detail || "—"}
                    {feature.evidence.length > 0 && (
                      <div className="muted" style={{ marginTop: 4 }}>
                        例: {feature.evidence[0]}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.routes.length > 0 && (
        <>
          <h3>静的経路</h3>
          <table>
            <thead>
              <tr>
                <th>宛先</th>
                <th>次に渡す先</th>
                <th>距離</th>
                <th>VRF</th>
              </tr>
            </thead>
            <tbody>
              {data.routes.map((route) => (
                <tr
                  key={`${route.vrf ?? ""}-${route.destination}-${route.next_hop ?? ""}`}
                >
                  <td>
                    {route.destination}
                    {route.is_default && (
                      <span className="muted">（既定）</span>
                    )}
                  </td>
                  <td>{route.next_hop ?? route.interface ?? "—"}</td>
                  <td>{route.distance ?? "既定"}</td>
                  <td>{route.vrf ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.ospf && (
        <>
          <h3>OSPF</h3>
          <p className="muted">
            プロセス {data.ospf.process_id ?? "—"} / ルータID{" "}
            {data.ospf.router_id ?? "—"}
            {data.ospf.passive_default && "（既定で passive）"}
          </p>
          <table>
            <thead>
              <tr>
                <th>エリア</th>
                <th>載せているもの</th>
              </tr>
            </thead>
            <tbody>
              {data.ospf.areas.map((area) => (
                <tr key={area.area}>
                  <td>{area.area}</td>
                  <td>{area.networks.join(" / ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.bgp && (
        <>
          <h3>BGP</h3>
          <p className="muted">
            AS {data.bgp.local_as ?? "—"} / ルータID {data.bgp.router_id ?? "—"}
          </p>
          <table>
            <thead>
              <tr>
                <th>相手</th>
                <th>相手の AS</th>
                <th>説明</th>
              </tr>
            </thead>
            <tbody>
              {data.bgp.neighbors.map((neighbor) => (
                <tr key={neighbor.address}>
                  <td>{neighbor.address}</td>
                  <td>{neighbor.remote_as ?? "—"}</td>
                  <td>{neighbor.description ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data.redundancy.length > 0 && (
        <>
          <h3>既定ゲートウェイの冗長</h3>
          <table>
            <thead>
              <tr>
                <th>ポート</th>
                <th>方式</th>
                <th>グループ</th>
                <th>仮想IP</th>
                <th>優先度</th>
              </tr>
            </thead>
            <tbody>
              {data.redundancy.map((entry) => (
                <tr key={`${entry.interface}-${entry.group}`}>
                  <td>{entry.interface}</td>
                  <td>{entry.protocol.toUpperCase()}</td>
                  <td>{entry.group ?? "—"}</td>
                  <td>{entry.virtual_ip ?? "—"}</td>
                  <td>{entry.priority ?? "既定"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
