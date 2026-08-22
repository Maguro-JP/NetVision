/**
 * 選んだ範囲の切り取り方。
 *
 * 行の途中で切ると、貼れない設定になる。選び方が雑でも、行の切れ目に
 * そろえてから渡す。
 */

import { describe, expect, it } from "vitest";

import { EDIT_SOURCE_LABEL, snapToLines } from "./ConfigEditor";

const CONFIG = [
  "interface Gi1/0/5",
  " switchport mode access",
  " no shutdown",
].join("\n");

describe("選んだ範囲", () => {
  it("行の途中で選んでも、その行ぜんぶを取る", () => {
    const start = CONFIG.indexOf("switchport") + 3;
    expect(snapToLines(CONFIG, start, start + 2)).toBe(
      " switchport mode access",
    );
  });

  it("複数行にまたがる選択は、両端の行ごと取る", () => {
    const start = CONFIG.indexOf("face");
    const end = CONFIG.indexOf("mode");
    expect(snapToLines(CONFIG, start, end)).toBe(
      "interface Gi1/0/5\n switchport mode access",
    );
  });

  it("何も選んでいなければ空", () => {
    expect(snapToLines(CONFIG, 5, 5)).toBe("");
  });

  it("最後の行を選んでも、はみ出さない", () => {
    const start = CONFIG.lastIndexOf("no shutdown");
    expect(snapToLines(CONFIG, start, CONFIG.length)).toBe(" no shutdown");
  });
});

describe("どう直したか", () => {
  it("規則なのかモデルなのかが分かるようにする", () => {
    expect(EDIT_SOURCE_LABEL.rule).toBe("決まった直し方で");
    expect(EDIT_SOURCE_LABEL.model).toBe("手元のモデルが");
    expect(EDIT_SOURCE_LABEL.prompt).toBe("直せませんでした");
  });
});
