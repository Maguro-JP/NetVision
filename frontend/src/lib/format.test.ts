import { describe, expect, it } from "vitest";

import { formatCurrency, formatDate, formatRemaining, formatTimestamp } from "./format";

describe("formatRemaining", () => {
  it("未登録は — を返す", () => {
    expect(formatRemaining(null)).toBe("—");
  });

  it("超過分は絶対値で表示する", () => {
    expect(formatRemaining(-1)).toBe("1日 超過");
    expect(formatRemaining(-400)).toBe("400日 超過");
  });

  it("当日は本日まで", () => {
    expect(formatRemaining(0)).toBe("本日まで");
  });

  it("60日未満は日数、それ以上は月・年に丸める", () => {
    expect(formatRemaining(45)).toBe("あと45日");
    expect(formatRemaining(90)).toBe("あと約2ヶ月");
    expect(formatRemaining(1300)).toBe("あと約3.6年");
  });
});

describe("formatDate / formatCurrency", () => {
  it("ISO 日付をスラッシュ区切りにする", () => {
    expect(formatDate("2027-03-31")).toBe("2027/03/31");
    expect(formatDate(null)).toBe("—");
  });

  it("金額を円記号と桁区切りで表示する", () => {
    expect(formatCurrency(480000)).toBe("¥480,000");
    expect(formatCurrency(null)).toBe("—");
  });
});

describe("formatTimestamp", () => {
  it("タイムゾーン付きの値を、その端末の時刻で出す", () => {
    // 13:37 UTC は JST の 22:37。ずれると変更履歴が9時間ずれて見える
    const text = formatTimestamp("2026-08-09T13:37:30+00:00");
    expect(text).toContain("2026");
    expect(text).toContain("22:37");
  });

  it("タイムゾーンが付いていない値は UTC として扱う", () => {
    // 付いていない文字列をそのまま Date に渡すと現地時刻と解釈される。
    // 古い形式が返ってきても、ずれたまま表示しない
    expect(formatTimestamp("2026-08-09T13:37:30")).toBe(
      formatTimestamp("2026-08-09T13:37:30Z"),
    );
  });

  it("空なら — を返す", () => {
    expect(formatTimestamp(null)).toBe("—");
  });

  it("読めない値はそのまま返す", () => {
    expect(formatTimestamp("不明")).toBe("不明");
  });
});
