/**
 * 線の引き方。人が定規で描くならどうするか、を規則にしたもの。
 *
 * 支社の図を見ると分かりやすい。支社ルータの真下に自拠点のスイッチがあり、
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

import { orthogonalBusPath, manhattanRoute } from "../components/PortLabeledEdge";
import type { TopologyEdge, TopologyGraph, TopologyNode } from "../api/types";
import {
  assignAnchors,
  keepLocalPositions,
  shiftFor,
  sideToward,
  storedFromDisplay,
  placementFromClick,
  positionsToSave,
} from "./TopologyPage";

function node(
  id: number,
  hostname: string,
  x: number,
  y: number,
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
    outside: false,
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
    const { anchors } = assignAnchors(branchOffice(6));
    expect(anchors.get(2)?.source).toBe("bottom-3");
    expect(anchors.get(2)?.target).toBe("top-3");
    // センターから支社へは、センターの下から出て支社の上へ入る
    expect(anchors.get(1)?.source).toBe("bottom-3");
    expect(anchors.get(1)?.target).toBe("top-3");
  });

  it("上にいる相手ほど、上の引き出し口から出る", () => {
    const graph = branchOffice(6);
    const { anchors } = assignAnchors(graph);
    const slots = graph.nodes
      .filter((n) => n.device_id >= 10)
      .sort((a, b) => a.y - b.y)
      .map((office) =>
        Number(anchors.get(office.device_id)?.source.split("-")[1]),
      );

    expect(slots).toHaveLength(6);
    for (let i = 1; i < slots.length; i += 1) {
      expect(slots[i]).toBeGreaterThan(slots[i - 1]);
    }
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
    const { lanes, anchors } = assignAnchors(graph);
    const edgeOf: Record<number, number> = { 2: 10, 3: 11, 4: 12 };
    const byY = dests
      .slice()
      .sort((a, b) => a.y - b.y)
      .map((d) => ({
        slot: Number(anchors.get(edgeOf[d.device_id])?.source.split("-")[1]),
        lane: lanes.get(edgeOf[d.device_id]) ?? 0,
      }));
    expect(byY[0].slot).toBeLessThan(byY[1].slot);
    expect(byY[1].slot).toBeLessThan(byY[2].slot);
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
  it("右から左へはハブ側で重ねてから扇に開き、縦横だけ通す", () => {
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
    const stackA = Number(a!.path.split(" ")[3].split(",")[0]);
    const stackB = Number(b!.path.split(" ")[3].split(",")[0]);
    expect(stackA).toBe(stackB);
    const peelA = Number(a!.path.split(" ")[5].split(",")[1]);
    const peelB = Number(b!.path.split(" ")[5].split(",")[1]);
    expect(peelA).toBe(80);
    expect(peelB).toBe(200);
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
});

describe("保存する座標", () => {
  it("広げた箱のずれは、見た目の位置へ戻る", () => {
    const shift = shiftFor(245);
    expect(shift).toBe(27.5);
    const display = { x: 83, y: 117 };
    const stored = storedFromDisplay(display, shift);
    expect(stored).toEqual({ x: 110.5, y: 117 });
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

  it("機器の顔ぶれが変わったときは、サーバの配置に従う", () => {
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

  it("保存・リセットのあとは、サーバの配置をそのまま使う", () => {
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
