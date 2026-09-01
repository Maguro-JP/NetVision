import { describe, expect, it } from "vitest";

import { sortVlanFindings } from "./VlanCheckPanel";
import type { VlanFinding } from "../api/types";

const item = (vlan_id: number, level: VlanFinding["level"]): VlanFinding => ({
  vlan_id,
  level,
  title: `VLAN ${vlan_id}`,
  why: "",
  evidence: [],
});

describe("VLAN の指摘の並び", () => {
  it("重いものから先に出す", () => {
    const sorted = sortVlanFindings([item(10, "low"), item(20, "medium"), item(30, "high")]);
    expect(sorted.map((f) => f.level)).toEqual(["high", "medium", "low"]);
  });

  it("同じ重さなら番号の小さい順。探すときに追える並びにする", () => {
    const sorted = sortVlanFindings([item(30, "low"), item(10, "low"), item(20, "low")]);
    expect(sorted.map((f) => f.vlan_id)).toEqual([10, 20, 30]);
  });
});
