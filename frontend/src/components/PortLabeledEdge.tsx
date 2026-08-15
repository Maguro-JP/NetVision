import { BaseEdge, EdgeLabelRenderer, Position, type EdgeProps } from "@xyflow/react";

import { MEDIA_COLOR, formatPort, mediaOf, type PortStyle } from "../lib/portlabel";

export interface PortEdgeData extends Record<string, unknown> {
  sourceInterface: string;
  targetInterface: string;
  /** 人が付けた線の名前。あればポート名より優先して中央に出す */
  manualLabel: string | null;
  portStyle: PortStyle;
  /** ポート名を線に沿わせて回転する */
  alongLine: boolean;
  /** ラベルを出さない（シンプル図で線だけにしたいとき） */
  hideLabels: boolean;
  /** 線を通す道のずらし幅。同じところから出る線が重ならないようにする */
  lane?: number;
  /** 束の中の順。0 が相手の並びに先頭 */
  laneRank?: number;
  laneCount?: number;
  warning: string | null;
}

/** ノード枠から、その先どのくらい離してラベルを置くか。 */
export const PORT_LABEL_GAP = 18;
/** 線に重ならないよう、線と垂直な向きへずらす幅。 */
export const PORT_LABEL_SIDE = 9;

const DIRECTION: Record<Position, [number, number]> = {
  [Position.Top]: [0, -1],
  [Position.Bottom]: [0, 1],
  [Position.Left]: [-1, 0],
  [Position.Right]: [1, 0],
};

/**
 * ポート名ラベルの位置と、箱から見て外側への基準点（transform の原点）。
 *
 * translate(-50%,-50%) で中央寄せすると、文字の半分が自分の箱へ戻って隠れる。
 * 引き出し方向の手前側を基準にして、箱の外側にだけ伸ばす。
 */
export function portLabelPlacement(
  x: number,
  y: number,
  position: Position,
  gap: number = PORT_LABEL_GAP,
  side: number = PORT_LABEL_SIDE,
): { x: number; y: number; anchor: string } {
  const [dx, dy] = DIRECTION[position] ?? [0, 0];
  const px = x + dx * gap - dy * side;
  const py = y + dy * gap + dx * side;
  const anchor =
    position === Position.Right
      ? "0%, -50%"
      : position === Position.Left
        ? "-100%, -50%"
        : position === Position.Bottom
          ? "-50%, 0%"
          : "-50%, -100%";
  return { x: px, y: py, anchor };
}

/**
 * 縦横だけの通り道。構成図の定石どおり、箱の間で束ねてから相手の高さで抜く。
 * 曲がった SmoothStep は、束の順が崩れると交差して見える。
 */
export function orthogonalBusPath(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: "top" | "bottom" | "left" | "right";
  targetFace: "top" | "bottom" | "left" | "right";
  rank: number;
  count: number;
  gap?: number;
}): { path: string; labelX: number; labelY: number } | null {
  const gap = input.gap ?? 16;
  const inset = 28;
  const rank = Math.max(0, input.rank);
  const steps = Math.max(0, input.count - 1);
  const sx = input.sourceX;
  const sy = input.sourceY;
  const tx = input.targetX;
  const ty = input.targetY;
  // 接続点が相手の高さに寄っている前提。ハブ側で一度重ねると折れが増えるだけなので、
  // 出口から相手の高さへ直角に扇を開く（紙の構成図と同じ）。
  if (input.sourceFace === "right" && input.targetFace === "left" && tx - sx > 40) {
    const usable = Math.max(0, tx - sx - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busX = tx - inset - rank * pitch;
    return {
      path: `M ${sx},${sy} L ${busX},${sy} L ${busX},${ty} L ${tx},${ty}`,
      labelX: busX,
      labelY: (sy + ty) / 2,
    };
  }
  if (input.sourceFace === "left" && input.targetFace === "right" && sx - tx > 40) {
    const usable = Math.max(0, sx - tx - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busX = tx + inset + rank * pitch;
    return {
      path: `M ${sx},${sy} L ${busX},${sy} L ${busX},${ty} L ${tx},${ty}`,
      labelX: busX,
      labelY: (sy + ty) / 2,
    };
  }
  if (input.sourceFace === "bottom" && input.targetFace === "top" && ty - sy > 40) {
    const usable = Math.max(0, ty - sy - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busY = ty - inset - rank * pitch;
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  if (input.sourceFace === "top" && input.targetFace === "bottom" && sy - ty > 40) {
    const usable = Math.max(0, sy - ty - inset * 2);
    const pitch = steps > 0 ? Math.min(gap, usable / steps) : 0;
    const busY = ty + inset + rank * pitch;
    return {
      path: `M ${sx},${sy} L ${sx},${busY} L ${tx},${busY} L ${tx},${ty}`,
      labelX: (sx + tx) / 2,
      labelY: busY,
    };
  }
  return null;
}

/** どの向きの引き出しでも縦横だけ。バスが使えないときも曲線にしない。 */
export function manhattanRoute(input: {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  sourceFace: "top" | "bottom" | "left" | "right";
  targetFace: "top" | "bottom" | "left" | "right";
  rank: number;
  count: number;
  gap?: number;
}): { path: string; labelX: number; labelY: number } {
  const bus = orthogonalBusPath(input);
  if (bus) return bus;

  const out = 16;
  const sx =
    input.sourceX + (input.sourceFace === "right" ? out : input.sourceFace === "left" ? -out : 0);
  const sy =
    input.sourceY + (input.sourceFace === "bottom" ? out : input.sourceFace === "top" ? -out : 0);
  const tx =
    input.targetX + (input.targetFace === "right" ? out : input.targetFace === "left" ? -out : 0);
  const ty =
    input.targetY + (input.targetFace === "bottom" ? out : input.targetFace === "top" ? -out : 0);
  const horizontalFirst = input.sourceFace === "left" || input.sourceFace === "right";
  const midX = horizontalFirst ? sx : tx;
  const midY = horizontalFirst ? ty : sy;
  return {
    path: `M ${input.sourceX},${input.sourceY} L ${sx},${sy} L ${midX},${midY} L ${tx},${ty} L ${input.targetX},${input.targetY}`,
    labelX: (sx + tx) / 2,
    labelY: (sy + ty) / 2,
  };
}

const FACE_OF: Record<Position, "top" | "bottom" | "left" | "right"> = {
  [Position.Top]: "top",
  [Position.Bottom]: "bottom",
  [Position.Left]: "left",
  [Position.Right]: "right",
};

/**
 * 結線1本。ポート名をそれぞれの機器側に置く。
 *
 * 1本の線に2つのポート名を並べると「左が上位」のような規則を覚える必要が出る。
 * 図の向きが変わればその規則は成り立たず、逆に読んでしまう。
 * ポートの隣にあれば規則は要らない。
 */
export default function PortLabeledEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data,
}: EdgeProps) {
  const info = (data ?? {}) as Partial<PortEdgeData>;
  const routed = manhattanRoute({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourceFace: FACE_OF[sourcePosition],
    targetFace: FACE_OF[targetPosition],
    rank: info.laneRank ?? 0,
    count: info.laneCount ?? 1,
  });
  const path = routed.path;
  const labelX = routed.labelX;
  const labelY = routed.labelY;
  const angle = (Math.atan2(targetY - sourceY, targetX - sourceX) * 180) / Math.PI;

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {!info.hideLabels && (
        <EdgeLabelRenderer>
          {info.manualLabel ? (
            <div
              className="topo-edge-label center"
              style={{
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
            >
              {info.manualLabel}
            </div>
          ) : (
            <>
              <PortLabel
                name={info.sourceInterface ?? ""}
                x={sourceX}
                y={sourceY}
                position={sourcePosition}
                angle={angle}
                style={info.portStyle ?? "short"}
                alongLine={info.alongLine ?? false}
                opacity={style?.opacity}
                color={typeof style?.stroke === "string" ? style.stroke : undefined}
              />
              <PortLabel
                name={info.targetInterface ?? ""}
                x={targetX}
                y={targetY}
                position={targetPosition}
                // 相手側から見た向き。ずらす向きが反転して、2つのラベルが
                // 線の反対側に出るので、機器が近くても重ならない
                angle={angle + 180}
                style={info.portStyle ?? "short"}
                alongLine={info.alongLine ?? false}
                opacity={style?.opacity}
                color={typeof style?.stroke === "string" ? style.stroke : undefined}
              />
            </>
          )}
          {info.warning && (
            <div
              className="topo-edge-label warning"
              style={{
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
            >
              ⚠ {info.warning}
            </div>
          )}
        </EdgeLabelRenderer>
      )}
    </>
  );
}

function PortLabel({
  name,
  x,
  y,
  position,
  angle,
  style,
  alongLine,
  opacity,
  color,
}: {
  name: string;
  x: number;
  y: number;
  position: Position;
  angle: number;
  style: PortStyle;
  alongLine: boolean;
  opacity?: number | string;
  color?: string;
}) {
  if (!name) return null;

  const placed = portLabelPlacement(x, y, position);
  const normalized = ((angle + 180) % 360) - 180;
  const flipped = normalized > 90 || normalized <= -90;
  // 上下逆さまの文字は読めないので、半回転させて向きを揃える
  const rotation = alongLine ? (flipped ? normalized + (normalized > 0 ? -180 : 180) : normalized) : 0;

  return (
    <div
      className="topo-edge-label port"
      style={{
        transform: `translate(${placed.anchor}) translate(${placed.x}px, ${placed.y}px) rotate(${rotation}deg)`,
        color: color ?? MEDIA_COLOR[mediaOf(name)],
        ...(opacity ? { opacity } : {}),
      }}
      title={name}
    >
      {formatPort(name, style)}
    </div>
  );
}
