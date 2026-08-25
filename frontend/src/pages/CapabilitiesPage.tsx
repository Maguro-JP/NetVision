import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import HouseStylePanel from "../components/HouseStylePanel";
import type { Capability } from "../api/types";

/**
 * 何にどこまで対応しているか。
 *
 * 「これは対応していますか」に、その都度口で答えるのは間違いのもとになる。
 * できることと同じ場所に、できないことも書いておく。
 *
 * 段階を3つに分けているのは、混ぜると「読めるから設定も起こせる」と
 * 読まれてしまうため。
 */

export const LEVEL_MARK: Record<string, string> = {
  full: "○",
  partial: "△",
  none: "—",
};

/** その行が、いま何に使えるかを一言で。表の記号だけだと伝わらない。 */
export function summarize(item: Capability): string {
  if (item.generate === "full") return "設計から Config まで起こせます";
  if (item.review === "full") return "読んで、粗の指摘までできます";
  if (item.read === "full") return "読んで台帳に載せられます";
  if (item.read === "partial") return "入っていることは分かります";
  return "扱っていません";
}

export default function CapabilitiesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });

  if (isLoading) return <p className="muted">読み込み中…</p>;
  if (!data) return null;

  return (
    <>
      <h1>対応機能</h1>
      <p className="subtitle">
        ネットワークの機能ごとに、どこまで扱えるかを並べています。できないことも
        同じ表に書いてあります。機種ごとの差は、対応した時点で別に足します。
      </p>

      <div className="cards">
        <div className="card">
          <div className="label">読む</div>
          <div className="value" style={{ fontSize: 16 }}>
            Config から拾って台帳と画面に出す
          </div>
        </div>
        <div className="card">
          <div className="label">検算</div>
          <div className="value" style={{ fontSize: 16 }}>
            読んだ内容の粗を指摘する
          </div>
        </div>
        <div className="card">
          <div className="label">起こす</div>
          <div className="value" style={{ fontSize: 16 }}>
            設計から Config を組み立てる
          </div>
        </div>
      </div>

      {data.areas.map((area) => (
        <div key={area}>
          <h2>{area}</h2>
          <table>
            <thead>
              <tr>
                <th>機能</th>
                <th style={{ width: 60 }}>読む</th>
                <th style={{ width: 60 }}>検算</th>
                <th style={{ width: 60 }}>起こす</th>
                <th>いま何に使えるか</th>
              </tr>
            </thead>
            <tbody>
              {data.items
                .filter((item) => item.area === area)
                .map((item) => (
                  <tr key={item.name}>
                    <td>{item.name}</td>
                    <td>{LEVEL_MARK[item.read]}</td>
                    <td>{LEVEL_MARK[item.review]}</td>
                    <td>{LEVEL_MARK[item.generate]}</td>
                    <td>
                      {summarize(item)}
                      <div className="muted" style={{ marginTop: 4 }}>
                        {item.note}
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ))}

      <p className="muted">○ はひととおり、△ は一部、— は扱っていません。</p>

      <HouseStylePanel />
    </>
  );
}
