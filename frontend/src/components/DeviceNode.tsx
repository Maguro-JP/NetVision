import { Handle, Position } from "@xyflow/react";
import { Fragment } from "react";

import type { DeviceType, Severity } from "../api/types";
import { DEVICE_TYPE_LABEL, SEVERITY_LABEL } from "../lib/format";
import { MEDIA_COLOR, mediaOf } from "../lib/portlabel";

export interface FaceHandle {
  id: string;
  side: Side;
  /** 辺の上の位置（0〜1）。スロットに丸めない。 */
  t: number;
}

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
  /** 箱の幅。上下の辺に線が多い機器は広げて、引き出し口の間隔を確保する */
  width?: number;
  /** 箱の高さ。左右の辺に線が多い機器は高くして、接続点とポート名の間隔を確保する */
  height?: number;
  /** 箱を広げたぶん左へ戻した量。保存時に足し戻す（TopologyPage の shiftFor）。 */
  shift?: number;
  /** 箱を高くしたぶん上へ戻した量。保存時に足し戻す。 */
  shiftY?: number;
  /** 実際の結線の接続点。辺の上の連続位置。 */
  faceHandles?: FaceHandle[];
  /** 種別の枠の色。人が凡例に合わせて変えられる */
  accent?: string;
  /** キャリア網の段（コア網 / メトロ / アクセス） */
  planeLabel?: string;
}

/** 各辺の引き出し口。辺ごとに3か所（手前 / 中央 / 奥）。 */
export const SIDES = ["top", "bottom", "left", "right"] as const;
export type Side = (typeof SIDES)[number];
/* 編集で新規に線を引くときの候補。表示中の結線は faceHandles の連続位置を使う。 */
export const SLOTS = [
  0.08, 0.14, 0.2, 0.26, 0.32, 0.38, 0.44, 0.5, 0.56, 0.62, 0.68, 0.74, 0.8, 0.86, 0.92,
];

const POSITION_OF: Record<Side, Position> = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
};

export function anchorId(side: Side, slot: number): string {
  return `${side}-${slot}`;
}

function handleStyle(side: Side, t: number): { left?: string; top?: string } {
  return side === "top" || side === "bottom"
    ? { left: `${t * 100}%` }
    : { top: `${t * 100}%` };
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
  // 結線がある辺は、対向位置に合わせた連続の接続点を出す。
  // 無い辺・編集中の新規結線用に、各辺の中央も残す。
  const used = new Set((data.faceHandles ?? []).map((h) => h.id));
  const anchors = [
    ...(data.faceHandles ?? []).map((handle) => ({
      id: handle.id,
      position: POSITION_OF[handle.side],
      style: handleStyle(handle.side, handle.t),
    })),
    ...SIDES.flatMap((side) => {
      const id = anchorId(side, 0);
      // faceHandles が side-0 を使っているときは中央の予備を重ねない
      if (used.has(id) && (data.faceHandles?.length ?? 0) > 0) {
        // 既にその id がある。編集用の中央は別 id にする
        const mid = `mid-${side}`;
        return data.editable
          ? [
              {
                id: mid,
                position: POSITION_OF[side],
                style: handleStyle(side, 0.5),
              },
            ]
          : [];
      }
      return [
        {
          id,
          position: POSITION_OF[side],
          style: handleStyle(side, 0.5),
        },
      ];
    }),
  ];

  return (
    <div
      style={{
        ...(data.width ? { width: data.width } : {}),
        // 高さは接続点の計算と同じ値を必ず固定する。中身で伸びると口の位置が狂う。
        ...(data.height
          ? { height: data.height, minHeight: data.height, maxHeight: data.height, overflow: "hidden" }
          : {}),
        ...(data.accent ? { borderColor: data.accent } : {}),
      }}
      className={
        `topo-node ${data.severity && !data.outside ? `sev-${data.severity}` : "simple"} ` +
        (data.outside ? "outside " : "") +
        (data.editable ? "editable " : "") +
        (selected ? "selected" : "")
      }
    >
      {/* 上下左右の各辺に引き出し口を置く。
          下の中央からしか出せないと、横に並べた機器の線が遠回りになる。
          相手の位置へ寄せて口を選び、線の折れと交差を減らす */}
      {anchors.map((anchor) => (
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
      {data.planeLabel && !data.outside && (
        <div className="muted topo-plane">{data.planeLabel}</div>
      )}
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
