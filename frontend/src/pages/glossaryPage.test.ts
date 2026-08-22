/**
 * 言葉の探し方。
 *
 * 言葉を知らない人は、言葉では探せない。空振りしたときに次の手を出さないと、
 * そこで手が止まって、結局くわしい人に聞くことになる。
 */

import { describe, expect, it } from "vitest";

import { hintFor } from "./GlossaryPage";

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
