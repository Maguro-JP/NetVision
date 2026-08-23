/**
 * 版と、いまの状態の見せ方。
 *
 * 台数や件数だけを出しても、まずいのかどうかが分からない。とくに
 * 「読めていない」ことを黙ると、無いものと読まれる。
 */

import { describe, expect, it } from "vitest";

import type { MibReport, ModelSpread } from "../api/types";
import { coverageOf, spreadOf } from "./OsMibPage";

function spread(over: Partial<ModelSpread> = {}): ModelSpread {
  return {
    model: "C9300-48P",
    os_name: "iosxe",
    devices: 3,
    versions: [],
    newest: "17.09.04a",
    behind: 2,
    unknown: 0,
    basis: "",
    mixed: true,
    ...over,
  };
}

function report(over: Partial<MibReport> = {}): MibReport {
  return {
    lines: 0,
    read: 0,
    unknown: 0,
    hostname: null,
    descr: null,
    vendor: null,
    model: null,
    os_version: null,
    serial: null,
    location: null,
    uptime: null,
    ports: [],
    values: [],
    differences: [],
    notes: [],
    ...over,
  };
}

describe("揃い方", () => {
  it("揃っていなければ、何台が古いかを言う", () => {
    expect(spreadOf(spread())).toContain("2 台");
  });

  it("揃っていれば、そう言い切る", () => {
    expect(spreadOf(spread({ mixed: false, behind: 0 }))).toContain(
      "揃っています",
    );
  });

  it("版が無ければ、比べられないと言う", () => {
    expect(spreadOf(spread({ newest: "" }))).toContain("比べられません");
  });
});

describe("読めた割合", () => {
  it("全部に意味があれば、件数だけでよい", () => {
    expect(coverageOf(report({ lines: 10, read: 10 }))).toBe(
      "10 件を読みました",
    );
  });

  it("意味を持っていない値は、隠さず数を出す", () => {
    const text = coverageOf(report({ lines: 10, read: 10, unknown: 3 }));
    expect(text).toContain("3 件");
  });

  it("何も無いときは、何も言わない", () => {
    expect(coverageOf(report())).toBe("");
  });
});
