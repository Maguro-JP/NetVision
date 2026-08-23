/**
 * 言葉の探し方。
 *
 * 言葉を知らない人は、言葉では探せない。空振りしたときに次の手を出さないと、
 * そこで手が止まって、結局くわしい人に聞くことになる。
 */

import { describe, expect, it } from "vitest";

import { groupByCategory, hintFor } from "./GlossaryPage";

describe("空振りしたとき", () => {
  it("困っていることでも探せると伝える", () => {
    expect(hintFor("ぱけっと", 0)).toContain("困っていること");
  });

  it("見つかっているときは何も言わない", () => {
    expect(hintFor("VLAN", 3)).toBe("");
  });

  it("まだ何も入れていないときは何も言わない", () => {
    expect(hintFor("", 0)).toBe("");
    expect(hintFor("   ", 0)).toBe("");
  });
});

describe("分野で束ねる", () => {
  const term = (name: string, category: string) => ({
    term: name,
    reading: name,
    plain: "",
    matters: "",
    category,
    also: [],
  });

  it("同じ分野をひとまとめにする", () => {
    const groups = groupByCategory([
      term("VLAN", "スイッチ（L2）"),
      term("OSPF", "ルーティング（L3）"),
      term("トランク", "スイッチ（L2）"),
    ]);
    expect(groups.map(([name]) => name)).toEqual(["スイッチ（L2）", "ルーティング（L3）"]);
    expect(groups[0][1].map((t) => t.term)).toEqual(["VLAN", "トランク"]);
  });

  it("分野が空でも捨てない", () => {
    expect(groupByCategory([term("なにか", "")])[0][0]).toBe("そのほか");
  });
});
