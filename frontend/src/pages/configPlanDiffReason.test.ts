import { describe, expect, it } from "vitest";

/**
 * 差分にコマンドが無いときの理由は、2 通りある。
 *
 * - 収束しなかった（出したら将来フルと合わない）
 * - その機種の書き方を持っていない（そもそも出せない）
 *
 * どちらも「コマンドが無い」だけは同じなので、見出しを1つに決め打ちすると、
 * 出せない機種に「収束しません」と言うことになる。直しようがない指摘に見える。
 */
type Diff = { verified: boolean; mismatches: string[]; warnings: string[] };

export function reasonFor(diff: Diff): "ok" | "not-converged" | "not-written" {
  if (diff.verified) return "ok";
  return diff.mismatches.length > 0 ? "not-converged" : "not-written";
}

describe("コマンドが出ないときの理由", () => {
  it("収束しなかったときは、そう言う", () => {
    expect(
      reasonFor({ verified: false, mismatches: ["interface Gi1"], warnings: [] }),
    ).toBe("not-converged");
  });

  it("書き方を持っていないときに、収束の話をしない", () => {
    expect(
      reasonFor({
        verified: false,
        mismatches: [],
        warnings: ["Cisco AireOS 無線コントローラ の投入コマンドは、まだ作り方を書いていません"],
      }),
    ).toBe("not-written");
  });

  it("出せたときは、どちらでもない", () => {
    expect(reasonFor({ verified: true, mismatches: [], warnings: [] })).toBe("ok");
  });
});
