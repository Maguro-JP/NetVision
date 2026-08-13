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

import type { TopologyEdge, TopologyGraph, TopologyNode } from "../api/types";
import { assignAnchors, keepLocalPositions, sideToward } from "./TopologyPage";

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

  it("近い相手ほど、外側の通り道を通る", () => {
    // 通り道のずれは相手から遠ざかる向きに効く。ずれが小さいほど外側
    const graph = branchOffice(6);
    const { lanes } = assignAnchors(graph);
    const offsets = graph.nodes
      .filter((n) => n.device_id >= 10)
      .sort((a, b) => a.y - b.y)
      .map((office) => lanes.get(office.device_id) ?? 0);

    for (let i = 1; i < offsets.length; i += 1) {
      expect(offsets[i]).toBeGreaterThan(offsets[i - 1]);
    }
  });

  it("通り道の束は、相手との間に収まる", () => {
    // はみ出すと線がいったん行き過ぎてから戻る。その戻りが交差に見える
    const graph = branchOffice(10);
    const { lanes } = assignAnchors(graph);
    const widest = Math.max(
      ...graph.edges.map((edge) => lanes.get(edge.id) ?? 0),
    );
    const nearest = Math.min(
      ...graph.nodes
        .filter((n) => n.device_id >= 10)
        .map((office) => office.y - 0),
    );
    expect(widest).toBeLessThan(nearest / 2);
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

  it("保存・リセットのあとは、サーバの配置をそのまま使う", () => {
    const incoming = [{ id: "1", position: { x: 0, y: 0 } }];
    const current = [{ id: "1", position: { x: 40, y: 80 } }];
    expect(keepLocalPositions(incoming, current, false)).toEqual(incoming);
  });
});
