/**
 * 覚えた流儀の見せ方。
 *
 * 割合の数字だけ出されても、それが「揃っている」のかは判断できない。
 */

import { describe, expect, it } from "vitest";

import { agreementLabel } from "./HouseStylePanel";

describe("どれくらい揃っていたか", () => {
  it("全部そろっていたなら、そう言い切る", () => {
    expect(agreementLabel(3, 3)).toBe("全部（3 台）そうしていました");
  });

  it("一部なら、台数で出す", () => {
    expect(agreementLabel(2, 3)).toBe("3 台のうち 2 台がそうしていました");
  });

  it("まだ何も見ていなければ何も出さない", () => {
    expect(agreementLabel(0, 0)).toBe("");
  });
});
