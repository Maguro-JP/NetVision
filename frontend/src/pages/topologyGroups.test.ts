import { describe, expect, it } from "vitest";

import { groupBox, updateGroupBounds, framesFromSites } from "./topologyGroups";

describe("機器を囲む枠", () => {
  it("箱の外側に余白を取る", () => {
    const box = groupBox([
      { x: 100, y: 80, width: 190, height: 78 },
      { x: 320, y: 80, width: 190, height: 78 },
    ]);
    expect(box).not.toBeNull();
    expect(box!.x).toBeLessThan(100);
    expect(box!.y).toBeLessThan(80);
    expect(box!.x + box!.width).toBeGreaterThan(510);
    expect(box!.y + box!.height).toBeGreaterThan(158);
  });

  it("機器を動かしたあと枠が追従する", () => {
    const next = updateGroupBounds([
      {
        id: "g-1",
        type: "group",
        position: { x: 0, y: 0 },
        data: { deviceIds: [1, 2], width: 80, height: 48 },
      },
      { id: "1", type: "device", position: { x: 200, y: 120 }, data: { width: 190 } },
      { id: "2", type: "device", position: { x: 420, y: 120 }, data: { width: 190 } },
    ]);
    const frame = next.find((node) => node.id === "g-1");
    expect(frame?.position.x).toBeLessThan(200);
    expect(frame?.position.y).toBeLessThan(120);
  });

  it("保存枠が無いときは、拠点ごとに仮の枠を置く", () => {
    const frames = framesFromSites([
      {
        id: "1",
        position: { x: 0, y: 200 },
        data: { width: 190, siteId: 2, siteName: "関西支社", outside: false },
      },
      {
        id: "2",
        position: { x: 0, y: 0 },
        data: { width: 190, siteId: 1, siteName: "西日本センター", outside: true },
      },
      {
        id: "3",
        position: { x: 400, y: 280 },
        data: { width: 190, siteId: 3, siteName: "京都営業所", outside: true },
      },
    ]);
    expect(frames).toHaveLength(3);
    expect(frames.map((frame) => frame.data.label).sort()).toEqual(
      ["京都営業所", "関西支社", "西日本センター"].sort(),
    );
    expect(frames.every((frame) => frame.selectable === false)).toBe(true);
  });

  it("1拠点だけなら仮の枠は出さない", () => {
    expect(
      framesFromSites([
        {
          id: "1",
          position: { x: 0, y: 0 },
          data: { width: 190, siteId: 1, siteName: "本社", outside: false },
        },
      ]),
    ).toEqual([]);
  });
});
