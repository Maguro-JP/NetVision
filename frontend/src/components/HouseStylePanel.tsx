import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { HouseStyle } from "../api/types";

/**
 * 取り込んだ Config から覚えた、その現場の流儀。
 *
 * 生成する Config を一般的な書き方のままにすると、現場の決めごとと食い違う。
 * かといって、裏で勝手に学んで裏で使うのがいちばん困る。覚えたことは
 * 台数と割合まで見せて、違っていれば人が気づけるようにする。
 */

/** どれくらい揃っていたかを、割合ではなく言葉で。数字だけだと判断できない。 */
export function agreementLabel(seen: number, total: number): string {
  if (total === 0) return "";
  if (seen >= total) return `全部（${total} 台）そうしていました`;
  return `${total} 台のうち ${seen} 台がそうしていました`;
}

export default function HouseStylePanel() {
  const { data } = useQuery({
    queryKey: ["conventions"],
    queryFn: api.conventions,
  });

  if (!data) return null;

  const house: HouseStyle = data;

  if (house.learned.length === 0) {
    return (
      <>
        <h2>この現場の流儀</h2>
        <div className="empty">
          まだ覚えていません。Config を2台ぶん以上取り込むと、VLAN
          の刻みやログの送り先、 どの機器にも入っている行を数えて、生成する
          Config に写します。
        </div>
      </>
    );
  }

  return (
    <>
      <h2>この現場の流儀</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        取り込んである {house.devices_seen} 台の Config
        から数えました。揃っていないものは
        覚えません。設計を起こすときに、ここに出ているものを使います。
      </p>
      <table>
        <thead>
          <tr>
            <th>覚えたこと</th>
            <th>中身</th>
            <th>どれくらい揃っていたか</th>
          </tr>
        </thead>
        <tbody>
          {house.learned.map((item) => (
            <tr key={item.topic}>
              <td>{item.topic}</td>
              <td>
                {item.value}
                {item.detail && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    {item.detail}
                  </div>
                )}
              </td>
              <td>{agreementLabel(item.seen, item.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {house.baseline_lines.length > 0 && (
        <>
          <h3>新しい機器に写す行</h3>
          <pre className="config-preview">
            {house.baseline_lines.join("\n")}
          </pre>
          <p className="muted">
            パスワードや鍵は覚えません。見せられないものを生成に混ぜると、人が確かめられなく
            なるためです。
          </p>
        </>
      )}
    </>
  );
}
