/**
 * メニューの並び。
 *
 * 探せなくなるのがいちばん困るので、行き先が全部どこかのまとまりに
 * 入っていること、奥のページでも上のタブが消えないことを見る。
 */

import { describe, expect, it } from "vitest";

import { SECTIONS, isCurrent, sectionFor } from "./nav";

describe("まとまりの数", () => {
  it("左に出すのは9つまで。上から順に読める量に収める", () => {
    expect(SECTIONS.length).toBeLessThanOrEqual(9);
  });

  it("入口は、そのまとまりの中の画面のどれか", () => {
    for (const section of SECTIONS) {
      if (section.items.length === 0) continue;
      expect(section.items.map((item) => item.to)).toContain(section.to);
    }
  });

  it("同じ行き先が2か所に出てこない", () => {
    const all = SECTIONS.flatMap((section) => section.items.map((item) => item.to));
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("いまどこにいるか", () => {
  it("画面から、そのまとまりが引ける", () => {
    expect(sectionFor("/vlans")?.label).toBe("台帳");
    expect(sectionFor("/remediation")?.label).toBe("セキュリティ");
  });

  it("奥のページでも、上のタブは消えない", () => {
    expect(sectionFor("/devices/12")?.label).toBe("台帳");
    expect(sectionFor("/scenarios/3")?.label).toBe("更改・計画");
    expect(isCurrent("/devices/12/edit", "/devices")).toBe(true);
  });

  it("名前が似ている画面に吸い込まれない", () => {
    expect(sectionFor("/config-plans")?.label).toBe("Config");
    expect(isCurrent("/config-plans", "/configs")).toBe(false);
  });

  it("メニューに出していない画面では、何も出さない", () => {
    expect(sectionFor("/space")).toBeUndefined();
    expect(sectionFor("/settings")).toBeUndefined();
  });
});
