/**
 * 動かした位置に合わせて、線の付き場所を付け直す。
 *
 * 手で描く構成図では、機器を動かすと線の付く場所も動く。決まった点に
 * 吸い付いたままだと、箱の裏側から線が出たり、遠回りしたりする。
 * PowerPoint の図形と同じで、線は「その機器に付いている」だけで、
 * どこに付くかは位置関係で決まる、という扱いにする。
 */

import type { Edge, Node } from "@xyflow/react";

import type { TopologyGraph } from "../api/types";
import type { DeviceNodeData } from "../components/DeviceNode";
import type { GroupFrameData } from "../components/GroupFrame";
import { assignAnchors, shiftFor, shiftYFor, storedFromDisplay, widthFor } from "./TopologyPage";

type FlowNode = Node<DeviceNodeData | GroupFrameData>;

export function restickEdges(
  nodes: FlowNode[],
  edges: Edge[],
  graph: TopologyGraph,
  simple: boolean,
): { nodes: FlowNode[]; edges: Edge[] } {
  const placed = new Map<number, { x: number; y: number }>();
  for (const node of nodes) {
    if (node.type === "group") continue;
    const data = node.data as DeviceNodeData;
    placed.set(
      Number(node.id),
      storedFromDisplay(node.position, data.shift ?? 0, data.shiftY ?? 0),
    );
  }
  if (placed.size === 0) return { nodes, edges };

  const moved: TopologyGraph = {
    ...graph,
    nodes: graph.nodes.map((node) => {
      const at = placed.get(node.device_id);
      return at ? { ...node, x: at.x, y: at.y } : node;
    }),
  };

  const { anchors, busiestTB, heights, faceHandles, lanes, laneRank, flipped } = assignAnchors(
    moved,
    { simple },
  );

  const nextNodes = nodes.map((node) => {
    if (node.type === "group") return node;
    const id = Number(node.id);
    const width = widthFor(busiestTB.get(id) ?? 0);
    const height = heights.get(id);
    const data = node.data as DeviceNodeData;
    return {
      ...node,
      data: {
        ...data,
        faceHandles: faceHandles.get(id),
        width,
        height: height ?? data.height,
        shift: shiftFor(width),
        shiftY: shiftYFor(height),
      },
    } as FlowNode;
  });

  const nextEdges = edges.map((edge) => {
    const found = anchors.get(Number(edge.id));
    if (!found) return edge;
    return {
      ...edge,
      sourceHandle: `s-${found.source}`,
      targetHandle: `t-${found.target}`,
      data: {
        ...edge.data,
        lane: lanes.get(Number(edge.id)) ?? 0,
        laneRank: laneRank.get(Number(edge.id))?.rank ?? 0,
        laneCount: laneRank.get(Number(edge.id))?.count ?? 1,
        sourceFlip: flipped.has(`${edge.id}:${edge.source}`),
        targetFlip: flipped.has(`${edge.id}:${edge.target}`),
      },
    };
  });

  return { nodes: nextNodes, edges: nextEdges };
}
