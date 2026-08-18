/**
 * 動かしたら線の付き場所も動く、という規則。
 *
 * 手で描く構成図では、機器を動かせば線の付く場所も変わる。決まった点に
 * 吸い付いたままだと、箱の裏から線が出たり、遠回りしたりする。
 */

import { describe, expect, it } from "vitest";

import type { TopologyGraph } from "../api/types";
import { restickEdges } from "./topologyRestick";

function graph(): TopologyGraph {
  const node = (id: number, x: number, y: number) =>
    ({
      device_id: id,
      hostname: `sw-${id}`,
      vendor: "Cisco",
      model: "X",
      device_type: "switch",
      site_name: null,
      mgmt_ip: null,
      severity: null,
      interface_count: 8,
      port_kinds: [],
      x,
      y,
      outside: false,
      hidden_peers: 0,
    }) as unknown as TopologyGraph["nodes"][number];
  return {
    layer: "physical",
    nodes: [node(1, 0, 0), node(2, 0, 400)],
    edges: [
      {
        id: 1,
        source: 1,
        target: 2,
        source_interface: "Gi0/1",
        target_interface: "Gi0/2",
        origin: "lldp",
        confirmed_both_ways: true,
        source_side: null,
        target_side: null,
      },
    ],
    unplaced_count: 0,
  } as unknown as TopologyGraph;
}

const flowNode = (id: number, x: number, y: number) => ({
  id: String(id),
  type: "device" as const,
  position: { x, y },
  data: { shift: 0, shiftY: 0 } as never,
});

describe("線の付き場所の付け直し", () => {
  it("真下にいた相手を真横へ動かすと、線は横の辺に付き替わる", () => {
    const below = restickEdges(
      [flowNode(1, 0, 0), flowNode(2, 0, 400)],
      [{ id: "1", source: "1", target: "2" }],
      graph(),
      false,
    );
    expect(below.edges[0].sourceHandle).toContain("bottom");

    const beside = restickEdges(
      [flowNode(1, 0, 0), flowNode(2, 600, 0)],
      [{ id: "1", source: "1", target: "2" }],
      graph(),
      false,
    );
    expect(beside.edges[0].sourceHandle).toContain("right");
    expect(beside.edges[0].targetHandle).toContain("left");
  });

  it("機器が無ければ何も変えない", () => {
    const edges = [{ id: "1", source: "1", target: "2" }];
    const out = restickEdges([], edges, graph(), false);
    expect(out.edges).toBe(edges);
  });
});
