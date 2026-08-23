import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { PortUsage, SubnetUsage } from "../api/types";

/**
 * 数えれば分かること。
 *
 * 「このセグメントはあと何台入るか」「空いている VLAN 番号はどれか」は、
 * 考えれば決まる話で、人が電卓を叩いたり、AI に推測させたりする筋のものではない。
 * 決まるものはここで数えて出し、決まらないもの（本当に空きポートか、など）は
 * 断定せずに、何をもってそう数えたかを添える。
 */

/** セグメントの余りを一言で。数字だけだと、多いのか少ないのか分からない。 */
export function roomFor(usage: SubnetUsage): string {
  if (usage.free === 0) return "空きがありません。ここには足せません";
  if (usage.crowded)
    return `残り ${usage.free} ですが、${usage.ratio}% まで埋まっています。採番を分ける相談が要ります`;
  return `あと ${usage.free} 台ぶん入ります`;
}

/** ポートの余りを一言で。0 のときは、増設先として選べないことまで言う。 */
export function portsLeft(usage: PortUsage): string {
  if (usage.free === 0) return "空きポートがありません。ここには挿せません";
  if (usage.free <= 2)
    return `残り ${usage.free} 本。増設するなら、これが最後です`;
  return `残り ${usage.free} 本`;
}

function Bar({ ratio }: { ratio: number }) {
  return (
    <div
      style={{
        background: "var(--border)",
        borderRadius: 3,
        height: 6,
        overflow: "hidden",
        width: 120,
      }}
    >
      <div
        style={{
          background: ratio >= 80 ? "var(--danger)" : "var(--accent)",
          height: "100%",
          width: `${Math.min(ratio, 100)}%`,
        }}
      />
    </div>
  );
}

export default function CalcPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["calc"],
    queryFn: api.calc,
  });

  if (isLoading) return <p className="muted">数えています…</p>;
  if (!data) return null;

  return (
    <>
      <h1>数えれば分かること</h1>
      <p className="subtitle">
        台帳と、Config
        から読み取った内容だけを数えています。推測は入っていないので、
        同じ内容なら何度見ても同じ答えになります。数え方は各行に書いてあります。
      </p>

      {data.notes.map((note) => (
        <p className="notice" key={note}>
          {note}
        </p>
      ))}

      {data.duplicates.length > 0 && (
        <section>
          <h2>同じアドレスが二か所にあります</h2>
          <p className="muted">
            片方が通信できなくなる形です。1台ずつ設定を見ていても気づけないので、
            突き合わせて出しています。
          </p>
          <table>
            <thead>
              <tr>
                <th>アドレス</th>
                <th>付いている場所</th>
              </tr>
            </thead>
            <tbody>
              {data.duplicates.map((item) => (
                <tr key={item.address}>
                  <td>{item.address}</td>
                  <td>{item.places.join(" / ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section>
        <h2>セグメントの空き</h2>
        <table>
          <thead>
            <tr>
              <th>セグメント</th>
              <th>VLAN</th>
              <th>使用</th>
              <th>残り</th>
              <th>数え方</th>
            </tr>
          </thead>
          <tbody>
            {data.subnets.map((item) => (
              <tr key={item.subnet}>
                <td>{item.subnet}</td>
                <td>
                  {item.vlan_id
                    ? `${item.vlan_id} ${item.vlan_name ?? ""}`
                    : "—"}
                </td>
                <td>
                  <Bar ratio={item.ratio} />
                  <span className="muted">
                    {item.used} / {item.capacity}（{item.ratio}%）
                  </span>
                </td>
                <td>{roomFor(item)}</td>
                <td className="muted">{item.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {data.vlans && (
        <section>
          <h2>VLAN 番号</h2>
          <p>
            使用中は {data.vlans.used_count} 個。次に使えるのは{" "}
            {data.vlans.free_next.join(", ") || "見つかりません"} です。
          </p>
          <p className="muted">{data.vlans.basis}</p>
        </section>
      )}

      <section>
        <h2>ポートの残り</h2>
        <table>
          <thead>
            <tr>
              <th>機器</th>
              <th>使用</th>
              <th>残り</th>
              <th>数え方</th>
            </tr>
          </thead>
          <tbody>
            {data.ports.map((item) => (
              <tr key={item.device_id}>
                <td>{item.hostname}</td>
                <td>
                  <Bar ratio={item.ratio} />
                  <span className="muted">
                    {item.in_use} / {item.total}（{item.ratio}%）
                  </span>
                </td>
                <td>{portsLeft(item)}</td>
                <td className="muted">{item.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {data.lags.length > 0 && (
        <section>
          <h2>束ねた線</h2>
          <p className="muted">
            片方が落ちたときに何が残るかを数えています。足りるかどうかは流れている
            量しだいなので、そこは言い切っていません。
          </p>
          <table>
            <thead>
              <tr>
                <th>機器</th>
                <th>束</th>
                <th>本数</th>
                <th>合計</th>
                <th>1本落ちたら</th>
              </tr>
            </thead>
            <tbody>
              {data.lags.map((item) => (
                <tr key={`${item.device_id}-${item.group}`}>
                  <td>{item.hostname}</td>
                  <td>{item.group}</td>
                  <td>{item.members}</td>
                  <td>{item.total_mbps ? `${item.total_mbps} Mbps` : "—"}</td>
                  <td>{item.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
