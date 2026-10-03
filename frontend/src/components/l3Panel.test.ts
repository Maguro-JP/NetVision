/**
 * L3 の指摘の見せ方。
 *
 * 重いものが下のほうにあると、そこまで読まない人が大事な指摘を見落とす。
 */

import { describe, expect, it } from "vitest";

import type { L3Finding } from "../api/types";
import { LEVEL_LABEL, sortFindings, sourcesLine, tools } from "./L3Panel";

function finding(level: L3Finding["level"], title: string): L3Finding {
  return { level, title, why: "理由", evidence: ["根拠"] };
}

describe("指摘の並び", () => {
  it("重いものから先に見せる", () => {
    const sorted = sortFindings([
      finding("low", "参考のもの"),
      finding("high", "急ぎのもの"),
      finding("medium", "確認するもの"),
    ]);
    expect(sorted.map((f) => f.title)).toEqual([
      "急ぎのもの",
      "確認するもの",
      "参考のもの",
    ]);
  });

  it("元の配列は触らない", () => {
    const original = [finding("low", "あとで"), finding("high", "いま")];
    sortFindings(original);
    expect(original[0].level).toBe("low");
  });
});

describe("度合いの呼び名", () => {
  it("英語のままにしない", () => {
    expect(LEVEL_LABEL.high).toBe("急ぎ");
    expect(LEVEL_LABEL.medium).toBe("確認");
    expect(LEVEL_LABEL.low).toBe("参考");
  });
});

describe("相手ごとに掛けている道具", () => {
  it("route-map と prefix-list をまとめない", () => {
    expect(tools("SET-LP", "CUST")).toBe("SET-LP / CUST");
    expect(tools(null, "CUST")).toBe("CUST");
  });

  it("何も掛けていないことが、そう読める", () => {
    // 空欄にすると「読めていない」と区別できない
    expect(tools(null, null)).toBe("絞っていません");
  });
});

describe("出典の行", () => {
  it("資料の名前を並べて出す", () => {
    expect(sourcesLine(["資料A", "資料B"])).toBe("出典: 資料A / 資料B");
  });

  it("出典の無い指摘や古い応答では行ごと出さない", () => {
    expect(sourcesLine([])).toBeNull();
    expect(sourcesLine(undefined)).toBeNull();
  });
});
