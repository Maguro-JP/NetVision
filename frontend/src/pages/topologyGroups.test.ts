import { describe, expect, it } from "vitest";

import { groupBox, updateGroupBounds } from "./topologyGroups";

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
});
