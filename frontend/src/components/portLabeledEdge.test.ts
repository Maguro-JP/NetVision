/**
 * 画面の線の引き回し。
 *
 * 図の上では「通過」と「接続」を見分けられない。箱の上を通った線は、
 * その機器にも繋がっていると読まれるので、避けて通す。
 */

import { describe, expect, it } from "vitest";

import { avoidRoute, segmentPiercesBox, sideBetween, type WireBox } from "./PortLabeledEdge";

const box = (x1: number, y1: number, x2: number, y2: number): WireBox => ({ x1, y1, x2, y2 });

describe("箱を避けて通る", () => {
  it("間にある箱の上を通らない", () => {
    const between = box(300, -60, 510, 60);
    const points = avoidRoute({ x: 0, y: 0 }, { x: 800, y: 0 }, [between]);
    for (let i = 0; i + 1 < points.length; i += 1) {
      expect(segmentPiercesBox(between, points[i], points[i + 1])).toBe(false);
    }
  });

  it("避けようがないときも、縦横の折れ線で返す", () => {
    const wall = box(-1000, -60, 1000, 60);
    const points = avoidRoute({ x: 0, y: 0 }, { x: 800, y: 0 }, [wall]);
    expect(points.length).toBeGreaterThanOrEqual(2);
    for (let i = 0; i + 1 < points.length; i += 1) {
      const a = points[i];
      const b = points[i + 1];
      expect(Math.abs(a.x - b.x) < 0.5 || Math.abs(a.y - b.y) < 0.5).toBe(true);
    }
  });
});

describe("線を出す辺", () => {
  it("右の列にいる相手へは横から出す", () => {
    // 対向の列は 380px 右。固定の距離（400px）で切っていたときは下から出ていた
    expect(sideBetween(380, 80)).toBe("right");
    expect(sideBetween(-380, 80)).toBe("left");
  });

  it("一段下の配下へは下から出す", () => {
    // 横にずれていても、縦の差のほうが大きければ下
    expect(sideBetween(300, 280)).toBe("bottom");
    expect(sideBetween(0, 280)).toBe("bottom");
  });

  it("同じ段なら横から出す", () => {
    expect(sideBetween(340, 0)).toBe("right");
  });
});
