/**
 * 表に出していない場所の入口。
 *
 * うっかり触って開くと、途中のものを使えるものだと思われる。
 * かといって開き方が難しすぎると、置いたことを忘れる。
 */

import { describe, expect, it } from "vitest";

import { TAPS_TO_OPEN, opensAt } from "./HiddenPage";

describe("入口", () => {
  it("一度触っただけでは開かない", () => {
    expect(opensAt(1)).toBe(false);
  });

  it("決めた回数で開く", () => {
    expect(opensAt(TAPS_TO_OPEN)).toBe(true);
  });
});
