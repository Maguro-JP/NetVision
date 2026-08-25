/**
 * 見た結果の要約。
 *
 * 何個も放り込んだとき、開く前に当たりを付けられないと、全部開くことになる。
 */

import { describe, expect, it } from "vitest";

import type { ConfigCheck, L3Finding } from "../api/types";
import { summaryOf } from "./ConfigCheckPage";

function finding(level: L3Finding["level"]): L3Finding {
  return { level, title: "t", why: "w", evidence: [] };
}

function check(over: Partial<ConfigCheck> = {}): ConfigCheck {
  return {
    filename: "a.txt",
    vendor: "cisco_ios",
    vendor_label: "Cisco IOS / IOS-XE",
    hostname: "sw-01",
    model: null,
    os_version: null,
    interfaces: 1,
    vlans: 1,
    routes: 1,
    features: [],
    findings: [],
    differences: [],
    notes: [],
    ...over,
  };
}

describe("1行の要約", () => {
  it("急ぎがあるなら、その件数を先に出す", () => {
    expect(
      summaryOf(check({ findings: [finding("high"), finding("low")] })),
    ).toBe("急ぎの指摘が 1 件");
  });

  it("急ぎが無ければ、指摘の件数を出す", () => {
    expect(
      summaryOf(check({ findings: [finding("low"), finding("medium")] })),
    ).toBe("指摘が 2 件");
  });

  it("指摘が無くても、流儀と違うところがあれば言う", () => {
    expect(summaryOf(check({ differences: [finding("low")] }))).toBe(
      "この現場のやり方と違うところが 1 件",
    );
  });

  it("何も無ければ、そう言い切る", () => {
    expect(summaryOf(check())).toBe("気になるところはありませんでした");
  });
});

describe("どうやって答えたか", () => {
  it("答えの出どころを、必ず言葉で出す", async () => {
    const { SOURCE_LABEL } = await import("./ConfigCheckPage");
    expect(SOURCE_LABEL.glossary).toBe("用語集から");
    expect(SOURCE_LABEL.model).toBe("手元のモデルが");
    expect(SOURCE_LABEL.prompt).toBe("答えられません");
  });
});
