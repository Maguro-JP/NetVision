import { describe, expect, it } from "vitest";

import { groupBox, groupBoxes, groupDepth, updateGroupBounds } from "./topologyGroups";

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

describe("入れ子の枠", () => {
  it("外側の枠は、中の枠を囲んだ外を回る", () => {
    const deviceBox = new Map([
      [1, { x: 0, y: 0, width: 100, height: 50 }],
      [2, { x: 300, y: 0, width: 100, height: 50 }],
    ]);
    const specs = [
      { id: 10, parentId: null, deviceIds: [] },
      { id: 11, parentId: 10, deviceIds: [1] },
      { id: 12, parentId: 10, deviceIds: [2] },
    ];
    const boxes = groupBoxes(specs, deviceBox);
    const outer = boxes.get(10)!;
    const inner = boxes.get(11)!;
    expect(outer.x).toBeLessThan(inner.x);
    expect(outer.y).toBeLessThan(inner.y);
    expect(outer.x + outer.width).toBeGreaterThan(inner.x + inner.width);
    // 機器を持たない外枠でも、中の枠から大きさが決まる
    expect(outer.width).toBeGreaterThan(400);
  });

  it("枠が輪になっていても図は出す", () => {
    const specs = [
      { id: 1, parentId: 2, deviceIds: [1] },
      { id: 2, parentId: 1, deviceIds: [] },
    ];
    const boxes = groupBoxes(specs, new Map([[1, { x: 0, y: 0, width: 10, height: 10 }]]));
    expect(boxes.size).toBeGreaterThan(0);
  });

  it("深さは外側が 0", () => {
    const specs = [
      { id: 1, parentId: null, deviceIds: [] },
      { id: 2, parentId: 1, deviceIds: [] },
      { id: 3, parentId: 2, deviceIds: [] },
    ];
    expect(groupDepth(specs, 1)).toBe(0);
    expect(groupDepth(specs, 3)).toBe(2);
  });
});
