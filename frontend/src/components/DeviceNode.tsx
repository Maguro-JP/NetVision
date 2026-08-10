import { Handle, Position } from "@xyflow/react";
import { Fragment } from "react";

import type { DeviceType, Severity } from "../api/types";
import { DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";
import { MEDIA_COLOR, mediaOf } from "../lib/portlabel";

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
  /** この機器で図に出てくるポートの種類。線のラベルを短くしても読めるようにする */
  portKinds: string[];
  /** 編集できる状態か。既定は不可（うっかり動かしてしまうため） */
  editable: boolean;
  /** 絞り込みの外にいて、対向として出しているだけの機器 */
  outside?: boolean;
  /** 同じ拠点にいて、代表1台にまとめた分の台数 */
  hiddenPeers?: number;
  /** 箱の幅。1つの辺に線が多い機器は広げて、引き出し口の間隔を確保する */
  width?: number;
}

/** 各辺の引き出し口。辺ごとに3か所（手前 / 中央 / 奥）。 */
export const SIDES = ["top", "bottom", "left", "right"] as const;
export type Side = (typeof SIDES)[number];
/* 各辺の引き出し口。線が増えても重ならないよう、辺あたり7か所置く。
   足りなくなったら箱の幅を広げて間隔を確保する（幅は data.width で渡す）。 */
export const SLOTS = [0.12, 0.25, 0.38, 0.5, 0.62, 0.75, 0.88];

const POSITION_OF: Record<Side, Position> = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
};

export function anchorId(side: Side, slot: number): string {
  return `${side}-${slot}`;
}

const ANCHORS = SIDES.flatMap((side) =>
  SLOTS.map((ratio, slot) => ({
    id: anchorId(side, slot),
    position: POSITION_OF[side],
    style:
      side === "top" || side === "bottom"
        ? { left: `${ratio * 100}%` }
        : { top: `${ratio * 100}%` },
  })),
);

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
      style={data.width ? { width: data.width } : undefined}
      className={
        `topo-node ${data.severity && !data.outside ? `sev-${data.severity}` : "simple"} ` +
        (data.outside ? "outside " : "") +
        (data.editable ? "editable " : "") +
        (selected ? "selected" : "")
      }
    >
      {/* 上下左右の各辺に3か所ずつ引き出し口を置く。
          下の中央からしか出せないと、横に並べた機器の線が遠回りになる。
          同じ辺に何本も来るときは、3か所に散らして重なりを避ける */}
      {ANCHORS.map((anchor) => (
        <Fragment key={anchor.id}>
          <Handle
            type="target"
            id={`t-${anchor.id}`}
            position={anchor.position}
            style={anchor.style}
            isConnectable={data.editable}
          />
          <Handle
            type="source"
            id={`s-${anchor.id}`}
            position={anchor.position}
            style={anchor.style}
            isConnectable={data.editable}
          />
        </Fragment>
      ))}

      <div className="topo-node-head">
        <span className="topo-glyph">{TYPE_GLYPH[data.deviceType]}</span>
        <span className="topo-hostname">{data.hostname}</span>
      </div>
      {/* ポートの種類を機器側に書いておく。線のラベルを番号だけにしても、
          ここを見れば何のポートか分かる（色は線のラベルと揃えてある） */}
      {/* 対向は簡易表示。見たいのは絞り込んだ側で、相手は「どこへ出ているか」が
          分かれば足りる。型番や保守状態まで出すと、肝心の側が読みにくくなる */}
      {data.outside ? (
        <div className="topo-node-body">
          <div className="muted">{data.siteName ?? "拠点未登録"}</div>
          {(data.hiddenPeers ?? 0) > 0 && (
            <div className="muted">ほかに {data.hiddenPeers} 台（この拠点内）</div>
          )}
        </div>
      ) : null}
      {!data.outside && data.portKinds.length > 0 && (
        <div className="topo-node-ports">
          {data.portKinds.map((kind, index) => (
            <span key={kind} style={{ color: MEDIA_COLOR[mediaOf(kind)] }}>
              {index > 0 && <span className="muted"> / </span>}
              {kind}
            </span>
          ))}
        </div>
      )}
      {/* シンプル表示では機器名だけを出す。現場で見る図に型番や保守状態が
          載っていると、読み違いのもとになるため */}
      {data.severity && !data.outside && (
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
