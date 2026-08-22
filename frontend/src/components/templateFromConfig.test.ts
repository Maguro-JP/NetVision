/**
 * 起こしたテンプレートの出どころの見せ方。
 *
 * 「この書式どこ見て書いたの」に答えられないテンプレートは保守できない。
 */

import { describe, expect, it } from "vitest";

import type { ConfigTemplateDraft } from "../api/types";
import { sourceLine } from "./TemplateFromConfig";

function draft(hostnames: string[]): ConfigTemplateDraft {
  return {
    name: "",
    vendor: "Cisco",
    body: "",
    variables: [],
    devices_seen: hostnames.length,
    hostnames,
    notes: [],
    skipped: 0,
  };
}

describe("何から起こしたか", () => {
  it("台数と機器名を出す", () => {
    expect(sourceLine(draft(["hq-sw-01", "hq-sw-02"]))).toBe(
      "2 台（hq-sw-01, hq-sw-02）の Config から",
    );
  });

  it("多いときは途中で切って、続きがあると分かるようにする", () => {
    const text = sourceLine(
      draft(["a-01", "a-02", "a-03", "a-04", "a-05", "a-06"]),
    );
    expect(text).toContain("ほか");
    expect(text).toContain("6 台");
  });
});
