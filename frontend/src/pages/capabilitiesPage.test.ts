/**
 * 対応状況の見せ方。
 *
 * 記号だけの表は読み違えられる。「読めるから設定も起こせる」と読まれると、
 * できないことを頼まれて、できないまま返すことになる。
 */

import { describe, expect, it } from "vitest";

import type { Capability } from "../api/types";
import { LEVEL_MARK, summarize } from "./CapabilitiesPage";

function item(over: Partial<Capability> = {}): Capability {
  return {
    area: "経路",
    name: "静的経路",
    read: "none",
    review: "none",
    generate: "none",
    note: "",
    ...over,
  };
}

describe("いま何に使えるか", () => {
  it("Config まで起こせるものは、そう言う", () => {
    expect(
      summarize(item({ read: "full", review: "full", generate: "full" })),
    ).toBe("設計から Config まで起こせます");
  });

  it("読めて指摘までなら、起こせるとは書かない", () => {
    const text = summarize(item({ read: "full", review: "full" }));
    expect(text).toBe("読んで、粗の指摘までできます");
  });

  it("一部しか読めないものは、そこまでだと分かるようにする", () => {
    expect(summarize(item({ read: "partial" }))).toBe(
      "入っていることは分かります",
    );
  });

  it("扱っていないものは、はっきりそう書く", () => {
    expect(summarize(item())).toBe("扱っていません");
  });
});

describe("記号", () => {
  it("できない欄を空白にしない", () => {
    expect(LEVEL_MARK.none).toBe("—");
    expect(LEVEL_MARK.partial).toBe("△");
    expect(LEVEL_MARK.full).toBe("○");
  });
});
