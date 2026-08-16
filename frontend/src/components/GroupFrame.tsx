import type { TopologyGroup } from "../api/types";
import { PLANE_LABEL } from "../lib/topologyColors";

export interface GroupFrameData extends Record<string, unknown> {
  label: string;
  plane: string;
  color: string;
  width: number;
  height: number;
  groupId: number;
  /** 外側の枠。無ければ null */
  parentId?: number | null;
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
        // 8% だと暗い画面で枠の中が背景に沈む。色で囲っていることが分かる濃さにする
        background: `${data.color}1f`,
      }}
    >
      <div className="topo-group-label" style={{ color: data.color }}>
        <span>
          {data.label}
          {data.plane !== "custom" ? ` · ${PLANE_LABEL[data.plane] ?? data.plane}` : ""}
        </span>
      </div>
    </div>
  );
}

export function groupNodeId(group: TopologyGroup): string {
  return `g-${group.id}`;
}
