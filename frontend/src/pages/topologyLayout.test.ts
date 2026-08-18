/**
 * 線の引き方。人が定規で描くならどうするか、を規則にしたもの。
 *
 * 支社の図を見ると分かりやすい。支社ルーターの真下に自拠点のスイッチがあり、
 * 右の列に配下の営業所が並ぶ。ここで営業所への線を下から出すと、真下へ伸びる
 * 線を何本もまたいでしまう。人はそうは描かない。横から出して、何本かを
 * 平行に沿わせて下ろし、相手の高さで1本ずつ抜く。基板の配線と同じ描き方で、
 * こうすると交差が1つも出ない。
 *
 * 交差が出ないことは、次の2つの並び順から導ける。試験ではそこを押さえる。
 *
 *   1. 引き出し口は、相手の並び順どおりに割り当てる
 *      （一番上の相手へは一番上のポートから）
 *   2. 通り道は、近い相手ほど外側（相手に近い側）を通す
 *
 * 1 が崩れると引き出し直後に線が入れ替わって交差する。
 * 2 が崩れると、手前で抜ける線が奥へ向かう線の通り道を横切る。
 */

import { describe, expect, it } from "vitest";

import { orthogonalBusPath, manhattanRoute, portLabelPlacement, routeWire, routePierceCount } from "../components/PortLabeledEdge";
import { Position } from "@xyflow/react";
import type { TopologyEdge, TopologyGraph, TopologyNode } from "../api/types";
import {
  assignAnchors,
  comparePortNames,
  keepLocalPositions,
  shiftFor,
  shiftYFor,
  sideToward,
  siteNameOnCanvas,
  storedFromDisplay,
  placementFromClick,
  positionsToSave,
  widthFor,
  heightFor,
  DEFAULT_WIDTH,
  DEFAULT_HEIGHT,
  DETAIL_HEIGHT,
} from "./TopologyPage";

function node(
  id: number,
  hostname: string,
  x: number,
  y: number,
  outside = false,
): TopologyNode {
  return {
    device_id: id,
    hostname,
    vendor: "Cisco",
    model: "ISR4451-X",
    device_type: "router",
    site_name: hostname,
    mgmt_ip: null,
    severity: null,
    interface_count: 24,
    port_kinds: [],
    x,
    y,
    outside,
    hidden_peers: 0,
  } as unknown as TopologyNode;
}

function link(id: number, source: number, target: number): TopologyEdge {
  return {
    id,
    source,
    target,
    source_interface: `GigabitEthernet0/${id}`,
    target_interface: "GigabitEthernet0/0",
    origin: "lldp",
    confirmed_both_ways: true,
    source_side: null,
    target_side: null,
  } as unknown as TopologyEdge;
}

/** 支社の図。上にセンター、真下に自拠点のスイッチ、右の列に営業所。 */
function branchOffice(peers: number): TopologyGraph {
  const nodes = [
    node(1, "center-rt", 0, -200),
    node(2, "br-rt", 0, 0),
    node(3, "br-sw", 0, 200),
  ];
  const edges = [link(1, 1, 2), link(2, 2, 3)];
  for (let i = 0; i < peers; i += 1) {
    nodes.push(node(10 + i, `office-${i}-rt`, 1025, 140 + i * 140));
    edges.push(link(10 + i, 2, 10 + i));
  }
  return {
    layer: "physical",
    nodes,
    edges,
    unplaced_count: 0,
  } as unknown as TopologyGraph;
}

describe("線をどの辺から出すか", () => {
  it("真下にいる相手へは下から出す", () => {
    expect(sideToward(node(1, "a", 0, 0), node(2, "b", 0, 200))).toBe("bottom");
    expect(sideToward(node(2, "b", 0, 200), node(1, "a", 0, 0))).toBe("top");
  });

  it("同じ段に並んでいる相手へは横から出す", () => {
    expect(sideToward(node(1, "a", 0, 0), node(2, "b", 300, 20))).toBe("right");
    expect(sideToward(node(2, "b", 300, 20), node(1, "a", 0, 0))).toBe("left");
  });

  it("段が違っても、横へ離れている相手へは横から出す", () => {
    // 支社から見た営業所。下から出すと自拠点へ伸びる線をまたいでしまう
    const branch = node(1, "br-rt", 0, 0);
    for (const y of [140, 560, 1400]) {
      expect(sideToward(branch, node(2, "office", 1025, y))).toBe("right");
    }
  });

  it("配下の営業所が何台あっても、全部が同じ辺から出る", () => {
    // 1台だけ下から出ると、そこだけ描き方が変わって図が読みにくくなる
    const graph = branchOffice(10);
    const branch = graph.nodes[1];
    const sides = graph.nodes
      .filter((n) => n.device_id >= 10)
      .map((office) => sideToward(branch, office));
    expect(new Set(sides)).toEqual(new Set(["right"]));
  });
});

describe("引き出し口と通り道", () => {
  it("1本しか出ていない辺は、真ん中から出す", () => {
    // 端に寄ると、真下の相手へ引いた線が斜めに折れる
    const { anchors, faceHandles } = assignAnchors(branchOffice(6));
    expect(anchors.get(2)?.source).toBe("bottom-0");
    expect(anchors.get(2)?.target).toBe("top-0");
    // センターから支社へは、センターの下から出て支社の上へ入る
    expect(anchors.get(1)?.source).toBe("bottom-0");
    expect(anchors.get(1)?.target).toBe("top-0");
    const hubHandle = faceHandles.get(2)?.find((h) => h.id === "bottom-0");
    expect(hubHandle?.t).toBeCloseTo(0.5, 1);
  });

  it("上にいる相手ほど、上の引き出し口から出る", () => {
    const graph = branchOffice(6);
    const { anchors, faceHandles } = assignAnchors(graph);
    const branchId = 2;
    const slots = graph.nodes
      .filter((n) => n.device_id >= 10)
      .sort((a, b) => a.y - b.y)
      .map((office) => {
        const edge = graph.edges.find(
          (e) => e.source === branchId && e.target === office.device_id,
        )!;
        const id = anchors.get(edge.id)?.source;
        return faceHandles.get(branchId)?.find((h) => h.id === id)?.t ?? 0;
      });

    expect(slots).toHaveLength(6);
    for (let i = 1; i < slots.length; i += 1) {
      expect(slots[i]).toBeGreaterThan(slots[i - 1]);
    }
    // 対向が箱の外に並ぶときは辺全体を使い、端に潰さない
    expect(slots[0]).toBeLessThan(0.2);
    expect(slots[slots.length - 1]).toBeGreaterThan(0.8);
  });

  it("相手の並び順どおりに通り道を振る（近くても順を入れ替えない）", () => {
    // ハブが列の途中にいると、近い順だと上下が入れ替わって交差する
    const hub = node(1, "core", 0, 200);
    const dests = [
      node(2, "top", 400, 0),
      node(3, "mid", 400, 180),
      node(4, "bot", 400, 400),
    ];
    const graph = {
      layer: "physical",
      nodes: [hub, ...dests],
      edges: [link(10, 1, 2), link(11, 1, 3), link(12, 1, 4)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { lanes, anchors, faceHandles } = assignAnchors(graph);
    const edgeOf: Record<number, number> = { 2: 10, 3: 11, 4: 12 };
    const byY = dests
      .slice()
      .sort((a, b) => a.y - b.y)
      .map((d) => {
        const id = anchors.get(edgeOf[d.device_id])?.source;
        return {
          t: faceHandles.get(1)?.find((h) => h.id === id)?.t ?? 0,
          lane: lanes.get(edgeOf[d.device_id]) ?? 0,
        };
      });
    expect(byY[0].t).toBeLessThan(byY[1].t);
    expect(byY[1].t).toBeLessThan(byY[2].t);
    expect(byY[0].lane).toBeLessThan(byY[1].lane);
    expect(byY[1].lane).toBeLessThan(byY[2].lane);
    expect(byY[1].lane).toBeGreaterThan(byY[0].lane);
  });

  it("通り道の束は、箱と箱の間に収まる", () => {
    // はみ出すと線がいったん行き過ぎてから戻る。その戻りが交差に見える
    const graph = branchOffice(10);
    const { lanes } = assignAnchors(graph);
    const widest = Math.max(...graph.edges.map((edge) => lanes.get(edge.id) ?? 0));
    const hub = graph.nodes.find((n) => n.device_id === 2)!;
    const nearestX = Math.min(
      ...graph.nodes.filter((n) => n.device_id >= 10).map((office) => office.x - hub.x),
    );
    expect(widest).toBeLessThan(nearestX / 2);
  });
});

describe("直交の通り道", () => {
  it("右から左へは出口から扇に開き、縦横だけ通す", () => {
    const a = orthogonalBusPath({
      sourceX: 0,
      sourceY: 0,
      targetX: 400,
      targetY: 80,
      sourceFace: "right",
      targetFace: "left",
      rank: 0,
      count: 3,
    });
    const b = orthogonalBusPath({
      sourceX: 0,
      sourceY: 40,
      targetX: 400,
      targetY: 200,
      sourceFace: "right",
      targetFace: "left",
      rank: 1,
      count: 3,
    });
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(a!.path.includes("C ")).toBe(false);
    // M sx,sy L bus,sy L bus,ty L tx,ty
    expect(a!.path.startsWith("M 0,0 L ")).toBe(true);
    expect(a!.path.endsWith(" L 400,80")).toBe(true);
    expect(b!.path.endsWith(" L 400,200")).toBe(true);
    const busA = Number(a!.path.split(" ")[3].split(",")[0]);
    const busB = Number(b!.path.split(" ")[3].split(",")[0]);
    expect(busA).toBeGreaterThan(busB);
  });

  it("上下が近いときは直交バスにしない", () => {
    expect(
      orthogonalBusPath({
        sourceX: 0,
        sourceY: 0,
        targetX: 20,
        targetY: 10,
        sourceFace: "right",
        targetFace: "left",
        rank: 0,
        count: 1,
      }),
    ).toBeNull();
  });

  it("バスが使えない向きでも縦横だけ通す", () => {
    const routed = manhattanRoute({
      sourceX: 0,
      sourceY: 0,
      targetX: 20,
      targetY: 10,
      sourceFace: "right",
      targetFace: "left",
      rank: 0,
      count: 1,
    });
    expect(routed.path.includes("C ")).toBe(false);
    expect(routed.path.startsWith("M ")).toBe(true);
    for (const part of routed.path.split(" ").slice(1)) {
      if (!part.includes(",")) continue;
      const [x, y] = part.split(",").map(Number);
      expect(Number.isFinite(x) && Number.isFinite(y)).toBe(true);
    }
  });

  it("途中の箱を貫く左右の線は下へ迂回する", () => {
    const obstacle = { x1: 200, y1: 0, y2: 80, x2: 400 };
    const routed = routeWire({
      sourceX: 500,
      sourceY: 40,
      targetX: 100,
      targetY: 40,
      sourceFace: "left",
      targetFace: "right",
      rank: 0,
      count: 1,
      obstacles: [obstacle],
      sourceBox: { x1: 500, y1: 0, x2: 690, y2: 80 },
      targetBox: { x1: -90, y1: 0, x2: 100, y2: 80 },
    });
    expect(routed.path.includes("C ")).toBe(false);
    const points = routed.path
      .split(/[ML]\s*/)
      .filter(Boolean)
      .map((part) => {
        const [x, y] = part.split(",").map(Number);
        return { x, y };
      });
    expect(routePierceCount(points, [obstacle])).toBe(0);
    // まっすぐ y=40 だと障害を貫くので、迂回で高さが変わる
    expect(points.some((p) => p.y > 80 || p.y < 0)).toBe(true);
  });

  it("同じ辺回りの横バスは通路上の箱の下を通る", () => {
    const hub = { x1: 200, y1: 200, x2: 390, y2: 332 };
    const near = { x1: 600, y1: 200, x2: 790, y2: 332 };
    const far = { x1: 920, y1: 200, x2: 1110, y2: 332 };
    const down = { x1: 600, y1: 450, x2: 790, y2: 582 };
    const routed = orthogonalBusPath({
      sourceX: 1015,
      sourceY: 332,
      targetX: 295,
      targetY: 332,
      sourceFace: "bottom",
      targetFace: "bottom",
      rank: 0,
      count: 1,
      obstacles: [hub, near, far, down],
    });
    expect(routed).not.toBeNull();
    const points = routed!.path
      .split(/[ML]\s*/)
      .filter(Boolean)
      .map((part) => {
        const [x, y] = part.split(",").map(Number);
        return { x, y };
      });
    expect(Math.max(0, points.length - 2)).toBeLessThanOrEqual(2);
    // 配下箱の下まで押し出す（口のすぐ下では配下への縦線と交差する）
    expect(points.some((p) => p.y >= down.y2 + 24)).toBe(true);
  });

  it("上下が揃っていれば縦一直線", () => {
    const routed = orthogonalBusPath({
      sourceX: 100,
      sourceY: 0,
      targetX: 100,
      targetY: 120,
      sourceFace: "bottom",
      targetFace: "top",
      rank: 0,
      count: 1,
    });
    expect(routed).not.toBeNull();
    expect(routed!.path).toBe("M 100,0 L 100,120");
  });
});

describe("保存する座標", () => {
  it("広げた箱のずれは、見た目の位置へ戻る", () => {
    const shift = shiftFor(245);
    expect(shift).toBe((245 - DEFAULT_WIDTH) / 2);
    const display = { x: 83, y: 117 };
    const stored = storedFromDisplay(display, shift);
    expect(stored).toEqual({ x: 83 + shift, y: 117 });
    expect({ x: stored.x - shift, y: stored.y }).toEqual(display);
  });

  it("保存でマス目に丸めない", () => {
    const shift = shiftFor(245);
    const display = { x: 83, y: 117 };
    expect(storedFromDisplay(display, shift).x - shift).toBe(display.x);
    expect(storedFromDisplay(display, shift).y).toBe(display.y);
  });

  it("保存は画面にある機器を全部送る（差分や対向だけ落とさない）", () => {
    expect(
      positionsToSave([
        { id: "1", position: { x: 40, y: 80 }, data: { shift: 0 } },
        { id: "2", position: { x: 200, y: 80 }, data: { shift: 0 } },
      ]),
    ).toEqual([
      { device_id: 1, x: 40, y: 80 },
      { device_id: 2, x: 200, y: 80 },
    ]);
  });
});

describe("動かした位置を表示の切り替えで消さない", () => {
  it("同じ機器が並んでいるときは、今の座標を残す", () => {
    const incoming = [
      { id: "1", position: { x: 0, y: 0 } },
      { id: "2", position: { x: 100, y: 0 } },
    ];
    const current = [
      { id: "1", position: { x: 40, y: 80 } },
      { id: "2", position: { x: 100, y: 0 } },
    ];
    expect(keepLocalPositions(incoming, current, true)).toEqual(current);
  });

  it("機器の顔ぶれが変わったときは、サーバーの配置に従う", () => {
    const incoming = [{ id: "3", position: { x: 0, y: 0 } }];
    const current = [{ id: "1", position: { x: 40, y: 80 } }];
    expect(keepLocalPositions(incoming, current, true)).toEqual(incoming);
  });

  it("台数が増えても、残っている機器の未保存位置は保つ", () => {
    const incoming = [
      { id: "1", position: { x: 0, y: 0 } },
      { id: "2", position: { x: 100, y: 0 } },
      { id: "3", position: { x: 200, y: 0 } },
    ];
    const current = [
      { id: "1", position: { x: 40, y: 80 } },
      { id: "2", position: { x: 100, y: 0 } },
    ];
    expect(keepLocalPositions(incoming, current, true)).toEqual([
      { id: "1", position: { x: 40, y: 80 } },
      { id: "2", position: { x: 100, y: 0 } },
      { id: "3", position: { x: 200, y: 0 } },
    ]);
  });

  it("保存・リセットのあとは、サーバーの配置をそのまま使う", () => {
    const incoming = [{ id: "1", position: { x: 0, y: 0 } }];
    const current = [{ id: "1", position: { x: 40, y: 80 } }];
    expect(keepLocalPositions(incoming, current, false)).toEqual(incoming);
  });
});

describe("図をクリックして機器を置く位置", () => {
  it("クリックした位置をそのまま使う", () => {
    expect(placementFromClick(23, 61)).toEqual({ x: 23, y: 61 });
    expect(placementFromClick(0, 0)).toEqual({ x: 0, y: 0 });
  });
});

describe("シンプル表示の拠点名", () => {
  it("対向はシンプルでも拠点名を残す", () => {
    expect(siteNameOnCanvas({ outside: true, site_name: "西日本センター" }, true)).toBe(
      "西日本センター",
    );
    expect(siteNameOnCanvas({ outside: false, site_name: "関西支社" }, true)).toBeNull();
    expect(siteNameOnCanvas({ outside: false, site_name: "関西支社" }, false)).toBe("関西支社");
  });
});

describe("箱幅・高さとポート名の画面座標", () => {
  it("広げた箱の中心は保存左上 + 既定幅/2 に留まる", () => {
    const width = widthFor(8)!;
    const shift = shiftFor(width);
    const stored = 0;
    const displayLeft = stored - shift;
    const center = displayLeft + width / 2;
    expect(center).toBe(stored + DEFAULT_WIDTH / 2);
  });

  it("左右に多いときは高さを伸ばし、中心の高さを保つ", () => {
    const height = heightFor(8);
    const shiftY = shiftYFor(height);
    const stored = 200;
    const displayTop = stored - shiftY;
    expect(displayTop + height / 2).toBe(stored + DEFAULT_HEIGHT / 2);
  });

  it("詳細表示の基準高さは中身分だけ高い", () => {
    expect(DETAIL_HEIGHT).toBeGreaterThan(DEFAULT_HEIGHT);
    expect(heightFor(2, DETAIL_HEIGHT)).toBe(DETAIL_HEIGHT);
    expect(heightFor(3, DETAIL_HEIGHT)).toBeGreaterThan(DETAIL_HEIGHT);
    expect(heightFor(8, DETAIL_HEIGHT)).toBeGreaterThan(DETAIL_HEIGHT);
  });

  it("右辺のポート名は中央寄せせず、箱の外側基準で置く", () => {
    const placed = portLabelPlacement(100, 40, Position.Right);
    expect(placed.anchor).toBe("0%, -50%");
    expect(placed.x).toBeGreaterThan(100);
  });

  it("右辺に多本数でも幅は広げず、高さで接続点の間隔を取る", () => {
    const hub = node(1, "hub", 0, 200);
    const peers = Array.from({ length: 12 }, (_, i) =>
      node(10 + i, `p${i}`, 400, i * 60),
    );
    const graph = {
      layer: "physical",
      nodes: [hub, ...peers],
      edges: peers.map((p, i) => link(100 + i, 1, p.device_id)),
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { busiestLR, busiestTB, faceHandles, heights, lanes } = assignAnchors(graph, {
      simple: true,
    });
    expect(busiestLR.get(1)).toBe(12);
    expect(busiestTB.get(1) ?? 0).toBe(0);
    expect(widthFor(busiestTB.get(1) ?? 0)).toBeUndefined();
    const height = heights.get(1)!;
    expect(height).toBeGreaterThan(DEFAULT_HEIGHT);
    const ts = faceHandles
      .get(1)!
      .filter((h) => h.side === "right")
      .map((h) => h.t)
      .sort((a, b) => a - b);
    expect(ts[0]).toBeLessThan(0.15);
    expect(ts[ts.length - 1]).toBeGreaterThan(0.85);
    // 辺全体に振ったうえで、隣どうしはポート名が被らない間隔
    for (let i = 1; i < ts.length; i += 1) {
      expect((ts[i] - ts[i - 1]) * height).toBeGreaterThanOrEqual(15);
    }
    // 幅を広げない分、通り道は箱の間のすき間に収める
    const widest = Math.max(0, ...lanes.values());
    expect(widest).toBeLessThan(400 / 2);
  });

  it("詳細表示でも対向の高さへ口が寄り、中央から階段にしない", () => {
    const hub = node(1, "edge-v", 0, 200);
    const upper = node(2, "core-a", 500, 80);
    const lower = node(3, "core-b", 500, 320);
    const graph = {
      layer: "physical",
      nodes: [hub, upper, lower],
      edges: [link(10, 1, 2), link(11, 1, 3)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { faceHandles, heights } = assignAnchors(graph, { simple: false });
    expect(heights.get(1)!).toBeGreaterThanOrEqual(DETAIL_HEIGHT);
    const rights = faceHandles
      .get(1)!
      .filter((h) => h.side === "right")
      .sort((a, b) => a.t - b.t);
    expect(rights).toHaveLength(2);
    // 上の対向は上側の口、下は下側。両方 0.5 付近に寄らない
    expect(rights[0].t).toBeLessThan(0.4);
    expect(rights[1].t).toBeGreaterThan(0.6);
  });

  it("同じ段の対向へは口の高さが揃い、他Hで中心がずれない", () => {
    const hub = node(1, "sw-a", 0, 200);
    const peer = node(2, "sw-b", 400, 200);
    const graph = {
      layer: "physical",
      nodes: [hub, peer],
      edges: [link(10, 1, 2)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { faceHandles, heights } = assignAnchors(graph, { simple: false });
    expect(heights.get(1)).toBe(DETAIL_HEIGHT);
    const right = faceHandles.get(1)!.find((h) => h.side === "right");
    expect(right?.t).toBeCloseTo(0.5, 2);
    // 口の画面 Y が対向中心と揃う（階段の元を潰す）
    const shiftY = shiftYFor(heights.get(1)!);
    const mouthY = hub.y - shiftY + right!.t * heights.get(1)!;
    expect(mouthY).toBeCloseTo(peer.y + DEFAULT_HEIGHT / 2, 5);
  });

  it("詳細の箱高さと接続点の分母が一致する", () => {
    const hub = node(1, "edge", 0, 100);
    const peer = node(2, "core", 500, 100);
    const graph = {
      layer: "physical",
      nodes: [hub, peer],
      edges: [link(1, 1, 2)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { heights } = assignAnchors(graph, { simple: false });
    expect(heights.get(1)).toBe(DETAIL_HEIGHT);
    expect(heights.get(2)).toBe(DETAIL_HEIGHT);
    const simple = assignAnchors(graph, { simple: true });
    expect(simple.heights.get(1)).toBe(DEFAULT_HEIGHT);
  });
  it("同じ相手への束は、辺の端へ分かれず近くに並ぶ", () => {
    const left = node(1, "sw-a", 0, 200);
    const right = node(2, "sw-b", 400, 200);
    const graph = {
      layer: "physical",
      nodes: [left, right],
      // ブリッジのように、同じ相手へ2本
      edges: [link(10, 1, 2), link(11, 1, 2)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { faceHandles, heights } = assignAnchors(graph, { simple: false });
    const height = heights.get(1)!;
    const ts = faceHandles
      .get(1)!
      .filter((h) => h.side === "right")
      .map((h) => h.t)
      .sort((a, b) => a - b);
    expect(ts).toHaveLength(2);
    // 中央付近にまとまっていて、辺の上下端まで開かない
    expect(ts[0]).toBeGreaterThan(0.25);
    expect(ts[1]).toBeLessThan(0.75);
    // ポート名が重ならないだけの間隔は空ける
    expect((ts[1] - ts[0]) * height).toBeGreaterThanOrEqual(11);
  });

  it("相手がばらばらの扇は、辺全体を使って順番に読ませる", () => {
    const hub = node(1, "hub", 300, 0);
    const peers = [2, 3, 4].map((id, index) => node(id, `leaf-${id}`, index * 200 + 100, 400));
    const graph = {
      layer: "physical",
      nodes: [hub, ...peers],
      edges: [link(10, 1, 2), link(11, 1, 3), link(12, 1, 4)],
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { faceHandles } = assignAnchors(graph, { simple: false });
    const ts = faceHandles
      .get(1)!
      .filter((h) => h.side === "bottom")
      .map((h) => h.t)
      .sort((a, b) => a - b);
    expect(ts).toHaveLength(3);
    expect(ts[2] - ts[0]).toBeGreaterThan(0.5);
  });
  it("同じ辺のポート名が、行の高さぶん離れて縦に並ぶ", () => {
    // 1台のハブに、右側の列から5台がぶら下がる（ポート名が右辺に積もる）
    const hub = node(1, "hub", 0, 400);
    const peers = [2, 3, 4, 5, 6].map((id, index) =>
      node(id, `peer-${id}`, 600, index * 160),
    );
    const graph = {
      layer: "physical",
      nodes: [hub, ...peers],
      edges: peers.map((peer, index) => link(10 + index, 1, peer.device_id)),
      unplaced_count: 0,
    } as unknown as TopologyGraph;
    const { faceHandles, heights } = assignAnchors(graph, { simple: false });
    const height = heights.get(1)!;
    const ys = faceHandles
      .get(1)!
      .filter((h) => h.side === "right")
      .map((h) => h.t * height)
      .sort((a, b) => a - b);
    expect(ys).toHaveLength(5);
    for (let index = 1; index < ys.length; index += 1) {
      // ラベルは 10px / line-height 1.2 / 上下余白1px で 14px
      expect(ys[index] - ys[index - 1]).toBeGreaterThanOrEqual(14);
    }
  });
});
describe("comparePortNames", () => {
  it("番号は数として比べる（Gi0/2 が Gi0/10 より先）", () => {
    const names = ["Gi0/10", "Gi0/2", "Gi0/1"];
    expect([...names].sort(comparePortNames)).toEqual(["Gi0/1", "Gi0/2", "Gi0/10"]);
  });

  it("接頭辞が違えば名前の順", () => {
    expect(comparePortNames("Eth1", "Gi0/1")).toBeLessThan(0);
  });
});

describe("ポート名の逃がし", () => {
  it("隣の線が近いと、線の反対側へ出す", () => {
    const near = portLabelPlacement(100, 100, Position.Right, 14, 12, true);
    const normal = portLabelPlacement(100, 100, Position.Right, 14, 12, false);
    // 通常は線の下、逃がしたものは線の上
    expect(normal.y).toBeGreaterThan(100);
    expect(near.y).toBeLessThan(100);
    // 線に沿った向きは変えない。どちらの機器のポートかが分からなくなる
    expect(near.x).toBe(normal.x);
  });

  it("上下の辺は左右へ逃がせないので、1行外へ出す", () => {
    const near = portLabelPlacement(100, 100, Position.Bottom, 14, 12, true);
    const normal = portLabelPlacement(100, 100, Position.Bottom, 14, 12, false);
    expect(near.y).toBeGreaterThan(normal.y);
    expect(near.anchor).toBe(normal.anchor);
  });
});
