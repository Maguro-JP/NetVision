import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { GlossaryTerm } from "../api/types";

/**
 * 画面に出てくる言葉の説明。
 *
 * このツールを使う人が、ネットワークの用語を知っているとは限らない。
 * 分からない言葉が1つあると、そこで手が止まり、結局くわしい人に聞くことになる。
 *
 * 探し方も2通り用意する。言葉を知っている人は言葉から、知らない人は
 * 「起きていること」から探す（「切り替わらない」で HSRP に当たる）。
 */

/**
 * 分野ごとに束ねる。
 *
 * 言葉の数が増えると、一覧は上から読めなくなる。探している言葉がどの分野かは
 * たいてい分かっているので、束ねておけばそこだけ見れば済む。
 * 並び順は入っている順（=説明の並び順）を保つ。分野が日によって入れ替わると探せない。
 */
export function groupByCategory(terms: GlossaryTerm[]): [string, GlossaryTerm[]][] {
  const groups = new Map<string, GlossaryTerm[]>();
  for (const term of terms) {
    const key = term.category || "そのほか";
    const bucket = groups.get(key);
    if (bucket) bucket.push(term);
    else groups.set(key, [term]);
  }
  return [...groups.entries()];
}

/** 入力から、探し方の見当を一言で返す。空振りしたときに次の手が要る。 */
export function hintFor(query: string, found: number): string {
  if (found > 0) return "";
  if (!query.trim()) return "";
  return "その言葉では見つかりませんでした。困っていること（「通じない」「切り替わらない」など）でも探せます。";
}

export default function GlossaryPage() {
  const [query, setQuery] = useState("");
  const { data: terms } = useQuery({
    queryKey: ["glossary", query],
    queryFn: () => api.glossary(query),
  });

  const found: GlossaryTerm[] = terms ?? [];

  return (
    <>
      <h1>言葉の説明</h1>
      <p className="subtitle">
        このツールに出てくる言葉を、たとえ話で説明します。正確さより、まず何の話なのかが
        分かることを取っています。困っていること（「通じない」「切り替わらない」）からも探せます。
      </p>

      <div className="toolbar">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="言葉、または困っていることで探す"
          style={{ minWidth: 320 }}
        />
        {query && <button onClick={() => setQuery("")}>全部見る</button>}
      </div>

      {hintFor(query, found.length) && (
        <div className="empty">{hintFor(query, found.length)}</div>
      )}

      {groupByCategory(found).map(([category, terms]) => (
        <section key={category}>
          <h2 style={{ marginBottom: 4 }}>{category}</h2>
          {terms.map((term) => (
            <div className="card" key={term.term} style={{ marginBottom: 12 }}>
              <h3 style={{ marginTop: 0 }}>
                {term.term}
                <span className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
                  {term.reading}
                </span>
              </h3>
              <p style={{ margin: "4px 0" }}>{term.plain}</p>
              <p style={{ margin: "4px 0" }}>
                <strong>狂うと何が起きるか</strong>: {term.matters}
              </p>
              {term.also.length > 0 && (
                <p className="muted" style={{ margin: "4px 0" }}>
                  こう呼ばれることもあります: {term.also.join(" / ")}
                </p>
              )}
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
