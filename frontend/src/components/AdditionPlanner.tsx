import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { AdditionPlan } from "../api/types";

/**
 * 「この機器の下に1台足したら、どう書くか」を起こす。
 *
 * 増設の設計は毎回同じ手順を踏む。空きポートを探し、使っていない VLAN 番号と
 * セグメントを決め、上位と下がりの両方に矛盾なく書く。その手順を、上位機器の
 * Config を根拠に踏んだ結果を見せる。
 *
 * 決めた値には必ず理由を出す。なぜその番号なのかが言えないと、人が確認しようが無い。
 */

export function routingLabel(routing: string): string {
  if (routing === "ospf") return "OSPF に載せる（上位に合わせる）";
  if (routing === "static") return "静的経路（直結なので上位に足す経路は不要）";
  return "上位の経路が読めないため、足す機器のデフォルト経路のみ";
}

/**
 * Config そのものと、その補足。
 *
 * Config には命令だけを入れる。説明や「要記入」を行に混ぜると、貼る前に
 * 消す手間が要るうえ、消し忘れがそのまま機器に入る。補足は下に並べる。
 */
function ConfigBlock({ body, notes }: { body: string; notes: string[] }) {
  return (
    <>
      {body ? (
        <pre className="config-preview">{body}</pre>
      ) : (
        <div className="empty">この書式では Config を起こしていません。</div>
      )}
      {notes.length > 0 && (
        <ul className="muted" style={{ marginTop: 6 }}>
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function AdditionPlanner({ deviceId }: { deviceId: number }) {
  const [open, setOpen] = useState(false);
  const [hostname, setHostname] = useState("");
  const [purpose, setPurpose] = useState("");
  const [vendor, setVendor] = useState("");
  const [vlanId, setVlanId] = useState("");
  const [subnet, setSubnet] = useState("");
  const [uplink, setUplink] = useState("");
  const [plan, setPlan] = useState<AdditionPlan | null>(null);

  const run = useMutation({
    mutationFn: () =>
      api.planAddition(deviceId, {
        hostname: hostname.trim(),
        purpose: purpose.trim(),
        vendor: vendor.trim(),
        vlan_id: vlanId ? Number(vlanId) : null,
        subnet: subnet.trim() || null,
        uplink_interface: uplink.trim() || null,
      }),
    onSuccess: setPlan,
  });

  return (
    <>
      <h2>この機器の下に1台足す</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        この機器の Config を読んで、空いている VLAN
        番号・セグメント・アドレス・ポートを決め、 上位側と新しい機器側の Config
        を両方起こします。既存が OSPF なら OSPF に合わせる、
        といったところまで見ています。決めた理由も一緒に出すので、そこを見て直してください。
      </p>
      <div className="toolbar">
        <button onClick={() => setOpen((value) => !value)}>
          {open ? "閉じる" : "足す機器を決める"}
        </button>
      </div>

      {open && (
        <>
          <div className="form-grid">
            <label>
              足す機器の名前
              <input
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                placeholder="floor3-sw-01"
              />
            </label>
            <label>
              用途（VLAN の名前になります）
              <input
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="FLOOR3"
              />
            </label>
            <label>
              メーカー（空なら上位と同じ書式）
              <input
                value={vendor}
                onChange={(e) => setVendor(e.target.value)}
                placeholder="Cisco"
              />
            </label>
            <label>
              VLAN 番号（空なら決めます）
              <input
                value={vlanId}
                onChange={(e) =>
                  setVlanId(e.target.value.replace(/[^0-9]/g, ""))
                }
                placeholder="30"
              />
            </label>
            <label>
              セグメント（空なら決めます）
              <input
                value={subnet}
                onChange={(e) => setSubnet(e.target.value)}
                placeholder="10.10.30.0/24"
              />
            </label>
            <label>
              挿すポート（空なら空きから選びます）
              <input
                value={uplink}
                onChange={(e) => setUplink(e.target.value)}
                placeholder="GigabitEthernet1/0/2"
              />
            </label>
          </div>
          <div className="actions">
            <button
              className="primary"
              disabled={!hostname.trim() || run.isPending}
              onClick={() => run.mutate()}
            >
              {run.isPending ? "組み立て中…" : "設計を起こす"}
            </button>
          </div>
          {run.error && (
            <div className="error">{(run.error as Error).message}</div>
          )}

          {plan && (
            <>
              <h3>決めたこと</h3>
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
              <p className="muted">経路の扱い: {routingLabel(plan.routing)}</p>

              {plan.warnings.length > 0 && (
                <div className="error" style={{ marginTop: 12 }}>
                  <strong>確かめてほしいこと</strong>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                    {plan.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              <h3>{plan.upstream_hostname} に足す分</h3>
              <ConfigBlock
                body={plan.upstream_config}
                notes={plan.upstream_notes}
              />

              <h3>{plan.hostname}（新規）</h3>
              <ConfigBlock
                body={plan.device_config}
                notes={plan.device_notes}
              />

              <h3>投入の前後で確かめること</h3>
              <ul>
                {plan.checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>
              <p className="muted">
                ここで作るのは下書きだけです。実機には投入しません。
                内容を確かめてから使ってください。
              </p>
            </>
          )}
        </>
      )}
    </>
  );
}
