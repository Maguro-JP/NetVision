/**
 * ことばで頼んだときの見せ方。
 *
 * 起こせたのか、まだ足りないのかが一目で分からないと、下の表を全部読むまで
 * 「これで終わりなのか」が分からない。
 */

import { describe, expect, it } from "vitest";

import type { AskResult } from "../api/types";
import { statusOf } from "./AskPage";

function result(over: Partial<AskResult> = {}): AskResult {
  return {
    reading: {
      site: null,
      site_id: null,
      upstream_hostname: null,
      upstream_device_id: null,
      new_hostname: null,
      purpose: "",
      notes: [],
    },
    upstream_choices: [],
    questions: [],
    plan: null,
    ...over,
  };
}

describe("いまどの状態か", () => {
  it("起こせたなら、そう言い切る", () => {
    expect(statusOf(result({ plan: {} as AskResult["plan"] }))).toBe(
      "設計を起こしました",
    );
  });

  it("機器を絞れないときは、選ぶ番だと分かるようにする", () => {
    const text = statusOf(
      result({
        upstream_choices: [{ device_id: 1, hostname: "sw-01", site: null }],
      }),
    );
    expect(text).toBe("どの機器のことか選んでください");
  });

  it("足りないときは、足りないと言う", () => {
    expect(statusOf(result({ questions: ["名前が要ります"] }))).toBe(
      "もう少し情報が要ります",
    );
  });

  it("まだ何も頼んでいなければ何も出さない", () => {
    expect(statusOf(null)).toBe("");
  });
});
