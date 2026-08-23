/**
 * 通り道の見せ方。
 *
 * どの段が何をしたのかが分からないと、出てきたものを確かめようがない。
 * とくに「AI を使っていない」ことと「AI が答えられず飛ばした」ことは、
 * 区別が付かないと判断を誤る。
 */

import { describe, expect, it } from "vitest";

import type { PipelineStage } from "../api/types";
import { detailOf, labelOf } from "./PipelinePage";

function stage(over: Partial<PipelineStage> = {}): PipelineStage {
  return {
    name: "下ごしらえ",
    kind: "logic",
    summary: "決まった値 2 件",
    skipped: "",
    model: null,
    ...over,
  };
}

describe("誰の仕事か", () => {
  it("ロジックの段はそう出す", () => {
    expect(labelOf(stage())).toBe("ロジック");
  });

  it("飛ばした AI の段は、動いた段と区別が付く", () => {
    expect(
      labelOf(stage({ kind: "ai", skipped: "モデルが使えないため" })),
    ).toContain("使っていません");
  });

  it("最後は人が受け取る", () => {
    expect(labelOf(stage({ kind: "human" }))).toBe("人");
  });
});

describe("その段の一言", () => {
  it("飛ばしたときは理由まで出す", () => {
    const text = detailOf(stage({ kind: "ai", skipped: "値を作ったため" }));
    expect(text).toContain("値を作ったため");
  });

  it("動いたときは、どのモデルかを出す", () => {
    expect(detailOf(stage({ kind: "ai", model: "local-7b" }))).toContain(
      "local-7b",
    );
  });

  it("ロジックの段は、そのまま出す", () => {
    expect(detailOf(stage())).toBe("決まった値 2 件");
  });
});
