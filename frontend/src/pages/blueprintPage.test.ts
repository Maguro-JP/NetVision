/**
 * 起こした構成の見せ方。
 *
 * 図を開く前に、機器がどの順で並んでいるかを1行で読めるようにする。
 * 「A の隣が FW で、その先がルータ」が分かれば、当たりが付く。
 */

import { describe, expect, it } from "vitest";

import type { Blueprint } from "../api/types";
import { chainOf } from "./BlueprintPage";

function plan(links: Blueprint["links"]): Blueprint {
  return {
    title: "",
    conditions: [],
    devices: [],
    links,
    segments: [],
    routes: [],
    decisions: [],
    warnings: [],
    configs: {},
    notes: {},
  };
}

function link(a: string, b: string): Blueprint["links"][number] {
  return {
    a_hostname: a,
    a_interface: "Gi0/0",
    b_hostname: b,
    b_interface: "Gi0/1",
    description: "",
  };
}

describe("機器の並び", () => {
  it("つながっている順に並べる", () => {
    const text = chainOf(
      plan([
        link("a-sw", "core-fw"),
        link("core-fw", "core-rt"),
        link("core-rt", "b-sw"),
      ]),
    );
    expect(text).toBe("a-sw — core-fw — core-rt — b-sw");
  });

  it("線が1本なら2台だけ", () => {
    expect(chainOf(plan([link("a-sw", "b-sw")]))).toBe("a-sw — b-sw");
  });

  it("線が無ければ何も出さない", () => {
    expect(chainOf(plan([]))).toBe("");
  });
});
