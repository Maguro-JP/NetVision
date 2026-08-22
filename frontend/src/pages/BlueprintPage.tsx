import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Blueprint } from "../api/types";

/**
 * 「A と B を、この条件で繋ぐならどうなるか」を起こす。
 *
 * 新しい拠点を組むとき、検証環境を作るとき、客先に案を見せるとき。
 * 決まっていないのは「間に何を挟むか」で、そこは条件から機械的に決まる。
 *
 * 案は図で見ないと判断できないので、そのままテスト拠点として台帳に置いて、
 * いつものトポロジー図で見られるようにしてある。本物と混ざらないよう
 * 拠点名に印が付き、まとめて消せる。
 */

/** 機器の並びを、1行で読めるようにする。図を開く前の当たりを付けるため。 */
export function chainOf(plan: Blueprint): string {
  if (plan.links.length === 0) return "";
  const names = [plan.links[0].a_hostname];
  for (const link of plan.links) {
    const next =
      link.a_hostname === names[names.length - 1] ? link.b_hostname : null;
    if (next && !names.includes(next)) names.push(next);
  }
  return names.join(" — ");
}

export default function BlueprintPage() {
  const queryClient = useQueryClient();
  const [aName, setAName] = useState("");
  const [bName, setBName] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const [base, setBase] = useState("");
  const [vendor, setVendor] = useState("Cisco");
  const [plan, setPlan] = useState<Blueprint | null>(null);
  const [placed, setPlaced] = useState<string | null>(null);

  const { data: conditions } = useQuery({
    queryKey: ["blueprintConditions"],
    queryFn: api.blueprintConditions,
  });
  const { data: testSites } = useQuery({
    queryKey: ["testSites"],
    queryFn: api.testSites,
  });

  const body = {
    a_name: aName.trim() || "A",
    b_name: bName.trim() || "B",
    conditions: checked,
    base: base.trim() || null,
    vendor,
  };

  const draw = useMutation({
    mutationFn: () => api.drawBlueprint(body),
    onSuccess: (data) => {
      setPlan(data);
      setPlaced(null);
    },
  });

  const place = useMutation({
    mutationFn: () => api.applyBlueprint(body),
    onSuccess: (data) => {
      setPlaced(data.site_name);
      queryClient.invalidateQueries({ queryKey: ["testSites"] });
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["sites"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  const remove = useMutation({
    mutationFn: (siteId: number) => api.removeTestSite(siteId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["testSites"] });
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["sites"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  const toggle = (name: string) =>
    setChecked((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );

  return (
    <>
      <h1>構成を起こす</h1>
      <p className="subtitle">
        つなぎたい2つと条件を選ぶと、間に挟む機器・結線・セグメント・Config
        を起こします。なぜその機器を置いたのかも一緒に出します。
      </p>

      <div className="form-grid">
        <label>
          つなぐ片方
          <input
            value={aName}
            onChange={(e) => setAName(e.target.value)}
            placeholder="honsha"
          />
        </label>
        <label>
          もう片方
          <input
            value={bName}
            onChange={(e) => setBName(e.target.value)}
            placeholder="shisha"
          />
        </label>
        <label>
          使うアドレス帯（空なら 10.90.0.0/16）
          <input
            value={base}
            onChange={(e) => setBase(e.target.value)}
            placeholder="10.90.0.0/16"
          />
        </label>
        <label>
          書式
          <select value={vendor} onChange={(e) => setVendor(e.target.value)}>
            <option value="Cisco">Cisco IOS 系</option>
            <option value="NEC">NEC QX（Comware 系）</option>
          </select>
        </label>
      </div>

      <h2>条件</h2>
      <div className="actions" style={{ flexWrap: "wrap" }}>
        {(conditions ?? []).map((condition) => (
          <label key={condition.name} style={{ marginRight: 16 }}>
            <input
              type="checkbox"
              checked={checked.includes(condition.name)}
              onChange={() => toggle(condition.name)}
            />{" "}
            {condition.label}
          </label>
        ))}
      </div>

      <div className="actions">
        <button
          className="primary"
          disabled={draw.isPending}
          onClick={() => draw.mutate()}
        >
          {draw.isPending ? "組み立て中…" : "案を出す"}
        </button>
        <button
          disabled={!plan || place.isPending}
          onClick={() => place.mutate()}
        >
          {place.isPending ? "置いています…" : "テスト拠点として置いてみる"}
        </button>
      </div>
      {draw.error && (
        <div className="error">{(draw.error as Error).message}</div>
      )}
      {place.error && (
        <div className="error">{(place.error as Error).message}</div>
      )}
      {placed && (
        <div className="card" style={{ marginTop: 12 }}>
          「{placed}」として台帳に置きました。
          <Link to="/topology"> トポロジー図で見る →</Link>
          <div className="muted" style={{ marginTop: 4 }}>
            本物の拠点と混ざらないよう、名前に印が付いています。下の一覧からまとめて消せます。
          </div>
        </div>
      )}

      {plan && (
        <>
          <h2>決めたこと</h2>
          <table>
            <thead>
              <tr>
                <th>項目</th>
                <th>決めた内容</th>
                <th>なぜそうしたか</th>
              </tr>
            </thead>
            <tbody>
              {plan.decisions.map((decision) => (
                <tr key={decision.topic}>
                  <td>{decision.topic}</td>
                  <td>{decision.choice}</td>
                  <td>{decision.why}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {plan.warnings.length > 0 && (
            <div className="error" style={{ marginTop: 12 }}>
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {plan.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}

          <h2>つながり</h2>
          <p className="muted">{chainOf(plan)}</p>
          <table>
            <thead>
              <tr>
                <th>片方</th>
                <th>ポート</th>
                <th>もう片方</th>
                <th>ポート</th>
                <th>何の線か</th>
              </tr>
            </thead>
            <tbody>
              {plan.links.map((link) => (
                <tr key={`${link.a_hostname}-${link.a_interface}`}>
                  <td>{link.a_hostname}</td>
                  <td>{link.a_interface}</td>
                  <td>{link.b_hostname}</td>
                  <td>{link.b_interface}</td>
                  <td>{link.description}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2>セグメント</h2>
          <table>
            <thead>
              <tr>
                <th>名前</th>
                <th>VLAN</th>
                <th>範囲</th>
                <th>端末が向く先</th>
                <th>いる機器</th>
              </tr>
            </thead>
            <tbody>
              {plan.segments.map((segment) => (
                <tr key={segment.subnet}>
                  <td>{segment.name}</td>
                  <td>{segment.vlan_id ?? "—"}</td>
                  <td>{segment.subnet}</td>
                  <td>{segment.gateway ?? "端末は置きません"}</td>
                  <td>{segment.members.join(" / ")}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {plan.routes.length > 0 && (
            <>
              <h2>経路</h2>
              <table>
                <thead>
                  <tr>
                    <th>機器</th>
                    <th>宛先</th>
                    <th>次に渡す先</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.routes.map((route) => (
                    <tr key={`${route.hostname}-${route.destination}`}>
                      <td>{route.hostname}</td>
                      <td>{route.destination}</td>
                      <td>{route.next_hop}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h2>Config</h2>
          {plan.devices.map((device) => (
            <div key={device.hostname}>
              <h3>
                {device.hostname}
                <span className="muted"> — {device.role}</span>
              </h3>
              <pre className="config-preview">
                {plan.configs[device.hostname]}
              </pre>
              <ul className="muted">
                {(plan.notes[device.hostname] ?? []).map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          ))}
          <p className="muted">
            ここで作るのは案です。実機には投入しません。内容を確かめてから使ってください。
          </p>
        </>
      )}

      <h2>置いてあるテスト拠点</h2>
      {(testSites ?? []).length === 0 ? (
        <div className="empty">まだありません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>拠点</th>
              <th>機器</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(testSites ?? []).map((site) => (
              <tr key={site.id}>
                <td>{site.name}</td>
                <td>{site.devices} 台</td>
                <td>
                  <button
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(site.id)}
                  >
                    機器ごと消す
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
