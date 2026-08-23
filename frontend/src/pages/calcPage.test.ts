/**
 * 数えた結果の言い方。
 *
 * 数字だけを出しても、多いのか少ないのか判断できない。特に「もう入らない」
 * ときは、そう言い切らないと増設先として選ばれてしまう。
 */

import { describe, expect, it } from "vitest";

import type { PortUsage, SubnetUsage } from "../api/types";
import { portsLeft, roomFor } from "./CalcPage";

function subnet(over: Partial<SubnetUsage> = {}): SubnetUsage {
  return {
    subnet: "10.0.10.0/24",
    capacity: 254,
    used: 4,
    free: 250,
    ratio: 1.6,
    crowded: false,
    vlan_id: 10,
    vlan_name: "USERS",
    devices: [],
    basis: "",
    ...over,
  };
}

function ports(over: Partial<PortUsage> = {}): PortUsage {
  return {
    device_id: 1,
    hostname: "acc-01",
    total: 48,
    in_use: 10,
    free: 38,
    ratio: 20.8,
    basis: "",
    ...over,
  };
}

describe("セグメントの余り", () => {
  it("空いていれば、あと何台入るかを言う", () => {
    expect(roomFor(subnet())).toContain("250");
  });

  it("埋まっていれば、ここには足せないと言い切る", () => {
    expect(roomFor(subnet({ free: 0, used: 254, ratio: 100 }))).toContain(
      "足せません",
    );
  });

  it("残っていても混んでいれば、採番の相談が要ると言う", () => {
    const text = roomFor(
      subnet({ free: 40, used: 214, ratio: 84.3, crowded: true }),
    );
    expect(text).toContain("採番");
  });
});

describe("ポートの余り", () => {
  it("空きがなければ、挿せないと言い切る", () => {
    expect(portsLeft(ports({ free: 0, in_use: 48, ratio: 100 }))).toContain(
      "挿せません",
    );
  });

  it("残りわずかなら、最後だと伝える", () => {
    expect(portsLeft(ports({ free: 1 }))).toContain("最後");
  });

  it("十分あるときは、本数だけでよい", () => {
    expect(portsLeft(ports())).toBe("残り 38 本");
  });
});
