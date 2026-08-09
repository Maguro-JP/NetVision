import { Handle, Position } from "@xyflow/react";

import type { DeviceType, Severity } from "../api/types";
import { DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";

export interface DeviceNodeData extends Record<string, unknown> {
  hostname: string;
  vendor: string;
  model: string;
  deviceType: DeviceType;
  /** null のときは付帯情報を出さない「シンプル表示」 */
  severity: Severity | null;
  siteName: string | null;
  mgmtIp: string | null;
  interfaceCount: number;
}

/** 種別ごとの記号。アイコン画像を持たずに一目で区別できるようにする。 */
const TYPE_GLYPH: Record<DeviceType, string> = {
  router: "◇",
  switch: "▭",
  firewall: "🛡",
  load_balancer: "⇄",
  wireless_ap: "((•))",
  wireless_controller: "◎",
  server: "▤",
  other: "▪",
};

export default function DeviceNode({
  data,
  selected,
}: {
  data: DeviceNodeData;
  selected?: boolean;
}) {
  return (
    <div
      className={
        `topo-node ${data.severity ? `sev-${data.severity}` : "simple"} ` +
        (selected ? "selected" : "")
      }
    >
      {/* 上下左右どこからでも線を引けるようにする */}
      <Handle type="target" position={Position.Top} />
      <Handle type="source" position={Position.Bottom} />
      <Handle type="target" position={Position.Left} id="left" />
      <Handle type="source" position={Position.Right} id="right" />

      <div className="topo-node-head">
        <span className="topo-glyph">{TYPE_GLYPH[data.deviceType]}</span>
        <span className="topo-hostname">{data.hostname}</span>
      </div>
      {/* シンプル表示では機器名だけを出す。現場で見る図に型番や保守状態が
          載っていると、読み違いのもとになるため */}
      {data.severity && (
        <>
          <div className="topo-node-body">
            <div>
              {data.vendor} {data.model}
            </div>
            <div className="muted">
              {DEVICE_TYPE_LABEL[data.deviceType]}
              {data.siteName ? ` / ${data.siteName}` : ""}
            </div>
            {data.mgmtIp && <div className="muted">{data.mgmtIp}</div>}
          </div>
          <div className={`topo-node-foot sev-text-${data.severity}`}>
            {SEVERITY_LABEL[data.severity]}
            {data.interfaceCount > 0 && (
              <span className="muted"> ・{data.interfaceCount}ポート</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
