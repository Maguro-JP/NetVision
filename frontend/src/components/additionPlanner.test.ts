/**
 * 足す機器の設計の見え方。
 *
 * 経路の扱いは、既存の流儀に合わせた結果として決まる。何に合わせたのかが
 * 一言で読めないと、そのまま投入されてしまう。
 */

import { describe, expect, it } from "vitest";

import { routingLabel } from "./AdditionPlanner";

describe("経路の扱いの一言", () => {
  it("OSPF なら、上位に合わせたと分かるように書く", () => {
    expect(routingLabel("ospf")).toContain("上位に合わせる");
  });

  it("静的なら、直結だから足す経路が要らないと分かるように書く", () => {
    expect(routingLabel("static")).toContain("直結");
  });

  it("読めなかったときは、そう分かるように書く", () => {
    expect(routingLabel("none")).toContain("読めない");
  });
});
