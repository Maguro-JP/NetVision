import type { TopologyGroup } from "../api/types";
import { PLANE_LABEL } from "../lib/topologyColors";

export interface GroupFrameData extends Record<string, unknown> {
  label: string;
  plane: string;
  color: string;
  width: number;
  height: number;
  groupId: number;
  deviceIds: number[];
}

export default function GroupFrame({
  data,
  selected,
}: {
  data: GroupFrameData;
  selected?: boolean;
}) {
  return (
    <div
      className={`topo-group${selected ? " selected" : ""}`}
      style={{
        width: data.width,
        height: data.height,
        borderColor: data.color,
        background: `${data.color}18`,
      }}
    >
      <div className="topo-group-label" style={{ color: data.color }}>
        {data.label}
        {data.plane !== "custom" ? ` · ${PLANE_LABEL[data.plane] ?? data.plane}` : ""}
      </div>
    </div>
  );
}

export function groupNodeId(group: TopologyGroup): string {
  return `g-${group.id}`;
}
