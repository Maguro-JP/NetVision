/**
 * 画面の線の描き方。
 *
 * 線を引くのはサーバー側の1か所だけにしてある。ここで見るのは、引かれた線を
 * そのまま描いているか、動かしている最中の仮の線が縦横に収まっているか。
 */

import { describe, expect, it } from "vitest";

import { previewRoute, segmentPiercesBox, sideBetween, type WireBox } from "./PortLabeledEdge";

const box = (x1: number, y1: number, x2: number, y2: number): WireBox => ({ x1, y1, x2, y2 });

describe("動かしている最中の仮の線", () => {
  it("斜めを出さない", () => {
    const points = previewRoute({ x: 0, y: 0 }, { x: 300, y: 200 }, "bottom")
      .path.split(/[ML]\s*/)
      .filter(Boolean)
      .map((part) => part.trim().split(",").map(Number));
    for (let i = 0; i + 1 < points.length; i += 1) {
      const [ax, ay] = points[i];
      const [bx, by] = points[i + 1];
      expect(Math.abs(ax - bx) < 0.5 || Math.abs(ay - by) < 0.5).toBe(true);
    }
  });
});

describe("箱の中を通ったかどうか", () => {
  it("端が辺に触れているだけなら、通ったことにしない", () => {
    // 口へ差し込む線は必ず辺に触れる。触れただけで貫きにすると、
    // どの線も「箱を通っている」ことになってしまう
    const target = box(100, 0, 300, 100);
    expect(segmentPiercesBox(target, { x: 0, y: 50 }, { x: 100, y: 50 })).toBe(false);
    expect(segmentPiercesBox(target, { x: 0, y: 50 }, { x: 200, y: 50 })).toBe(true);
  });
});

describe("線を出す辺", () => {
  it("右の列にいる相手へは横から出す", () => {
    expect(sideBetween(380, 80)).toBe("right");
    expect(sideBetween(-380, 80)).toBe("left");
  });

  it("一段下の配下へは下から出す", () => {
    expect(sideBetween(300, 280)).toBe("bottom");
    expect(sideBetween(0, 280)).toBe("bottom");
  });

  it("同じ段なら横から出す", () => {
    expect(sideBetween(340, 0)).toBe("right");
  });
});
