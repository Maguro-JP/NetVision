/**
 * 経路から推定した線の見せ方。
 *
 * 「まとめて入れる」で何本入るのかが先に分からないと、押したあとで
 * 図が増えすぎたことに気づくことになる。
 */

import { describe, expect, it } from "vitest";

import type { LinkCandidate } from "../api/types";
import { CONFIDENCE_LABEL, sureCount } from "./LinkCandidates";

function candidate(confidence: LinkCandidate["confidence"]): LinkCandidate {
  return {
    a_device_id: 1,
    a_hostname: "a",
    a_interface: "Gi0/0",
    b_device_id: 2,
    b_hostname: "b",
    b_interface: "Gi0/0",
    segment: "10.0.0.0/30",
    confidence,
    why: "",
    evidence: [],
  };
}

describe("まとめて入れる本数", () => {
  it("言い切れるものだけ数える", () => {
    expect(
      sureCount([candidate("high"), candidate("medium"), candidate("high")]),
    ).toBe(2);
  });

  it("候補がまだ無ければ 0", () => {
    expect(sureCount(undefined)).toBe(0);
    expect(sureCount([])).toBe(0);
  });
});

describe("確からしさの呼び名", () => {
  it("英語のままにしない", () => {
    expect(CONFIDENCE_LABEL.high).toBe("確か");
    expect(CONFIDENCE_LABEL.medium).toBe("たぶん");
  });
});
