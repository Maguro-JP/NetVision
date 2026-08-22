/**
 * 両端の突き合わせの見せ方。
 *
 * 何本見て、何本見られなかったのかを言わないと、全部見たと思われる。
 */

import { describe, expect, it } from "vitest";

import type { LinkFinding } from "../api/types";
import { coverageOf, sortLinkFindings } from "./LinkCheckPanel";

function finding(level: LinkFinding["level"], title: string): LinkFinding {
  return {
    link_id: 1,
    a_hostname: "a",
    a_interface: "Gi1/0/1",
    b_hostname: "b",
    b_interface: "Gi1/0/1",
    level,
    title,
    why: "",
    evidence: [],
  };
}

describe("どこまで見たか", () => {
  it("全部見られたなら、そう言い切る", () => {
    expect(coverageOf(12, 12)).toBe("12 本すべての両端を見ました");
  });

  it("見られなかった分があるなら、その理由まで言う", () => {
    const text = coverageOf(12, 5);
    expect(text).toContain("12 本のうち 5 本");
    expect(text).toContain("読めていません");
  });

  it("線が無ければ何も言わない", () => {
    expect(coverageOf(0, 0)).toBe("");
  });
});

describe("指摘の並び", () => {
  it("重いものから先に見せる", () => {
    const sorted = sortLinkFindings([
      finding("low", "参考"),
      finding("high", "急ぎ"),
      finding("medium", "確認"),
    ]);
    expect(sorted.map((f) => f.title)).toEqual(["急ぎ", "確認", "参考"]);
  });
});
