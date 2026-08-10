/**
 * 機器一覧の並び。
 *
 * Config を取り込んだ直後は、増えた機器を確かめたい。名前順のままだと
 * 何十行の途中に紛れて見つけられないので、新しく載ったものを先頭へ寄せる。
 */

import { describe, expect, it } from "vitest";

import type { Device } from "../api/types";
import { sortForList } from "./DeviceTable";

function device(hostname: string, addedDaysAgo: number): Device {
  const created = new Date(Date.now() - addedDaysAgo * 24 * 60 * 60 * 1000);
  return {
    id: hostname.length,
    hostname,
    vendor: "Cisco",
    model: "C9300-48P",
    device_type: "switch",
    status: "active",
    missing_fields: [],
    created_at: created.toISOString(),
    lifecycle: { overall_severity: "ok" },
  } as unknown as Device;
}

describe("機器一覧の並び", () => {
  it("新しく載った機器が上に来る", () => {
    const rows = sortForList([device("aaa-sw-01", 400), device("zzz-sw-01", 1)]);
    expect(rows.map((d) => d.hostname)).toEqual(["zzz-sw-01", "aaa-sw-01"]);
  });

  it("新着どうし・それ以外どうしは名前順（日によって並びが変わらないように）", () => {
    const rows = sortForList([
      device("b-new", 1),
      device("z-old", 400),
      device("a-new", 2),
      device("c-old", 300),
    ]);
    expect(rows.map((d) => d.hostname)).toEqual(["a-new", "b-new", "c-old", "z-old"]);
  });

  it("載った日時が分からない機器も落とさない", () => {
    const unknown = { ...device("x-sw-01", 1), created_at: null };
    expect(sortForList([unknown]).map((d) => d.hostname)).toEqual(["x-sw-01"]);
  });

  it("元の配列を書き換えない", () => {
    const input = [device("z", 400), device("a", 1)];
    sortForList(input);
    expect(input.map((d) => d.hostname)).toEqual(["z", "a"]);
  });
});
