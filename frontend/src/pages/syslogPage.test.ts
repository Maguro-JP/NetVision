/**
 * ログの読み取り結果の見せ方。
 *
 * 全部読めたのか、読めなかった行があるのか。いつからいつまでの話なのか。
 * ここを言わないまま件数だけ出すと、無い事象を「起きていない」と読まれる。
 */

import { describe, expect, it } from "vitest";

import type { LogReport } from "../api/types";
import { coverageOf, periodOf } from "./SyslogPage";

function report(over: Partial<LogReport> = {}): LogReport {
  return {
    lines: 0,
    parsed: 0,
    unreadable: 0,
    first_at: null,
    last_at: null,
    by_severity: {},
    by_host: [],
    patterns: [],
    known_hosts: [],
    unknown_hosts: [],
    notes: [],
    ...over,
  };
}

describe("読めた割合", () => {
  it("全部読めたときはそう言う", () => {
    expect(coverageOf(report({ lines: 12, parsed: 12 }))).toBe("12 行すべてを読みました");
  });

  it("読めない行があるときは、その数を隠さない", () => {
    const text = coverageOf(report({ lines: 12, parsed: 9, unreadable: 3 }));
    expect(text).toContain("9 行を読みました");
    expect(text).toContain("3 行は読めていません");
  });

  it("行が無いときは何も言わない", () => {
    expect(coverageOf(report())).toBe("");
  });
});

describe("期間", () => {
  it("時刻が読めなければ、読めないと言う", () => {
    expect(periodOf(report({ lines: 3, parsed: 3 }))).toContain("読み取れません");
  });

  it("始めと終わりが同じなら、ひとつだけ出す", () => {
    const at = "2026-08-22T01:00:00";
    const text = periodOf(report({ first_at: at, last_at: at }));
    expect(text).not.toContain("〜");
  });

  it("幅があるときは、いつからいつまでかを出す", () => {
    const text = periodOf(report({ first_at: "2026-08-22T01:00:00", last_at: "2026-08-22T03:00:00" }));
    expect(text).toContain("〜");
  });
});
