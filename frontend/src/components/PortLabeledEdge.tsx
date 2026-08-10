import { BaseEdge, EdgeLabelRenderer, Position, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

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
  warning: string | null;
}

/** ノード枠から、その先どのくらい離してラベルを置くか。 */
const GAP = 14;
/** 線に重ならないよう、線と垂直な向きへずらす幅。 */
const SIDE = 9;

const DIRECTION: Record<Position, [number, number]> = {
  [Position.Top]: [0, -1],
  [Position.Bottom]: [0, 1],
  [Position.Left]: [-1, 0],
  [Position.Right]: [1, 0],
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
  // 同じところから何本も出るときは、線ごとに通り道をずらす。
  // 基板の配線と同じで、重ねずに沿わせて引くと本数が読める
  const lane = info.lane ?? 0;
  const vertical = sourcePosition === Position.Top || sourcePosition === Position.Bottom;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    ...(lane
      ? vertical
        ? { centerY: (sourceY + targetY) / 2 + (sourceY < targetY ? -lane : lane) }
        : { centerX: (sourceX + targetX) / 2 + (sourceX < targetX ? -lane : lane) }
      : {}),
  });
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
}: {
  name: string;
  x: number;
  y: number;
  position: Position;
  angle: number;
  style: PortStyle;
  alongLine: boolean;
  opacity?: number | string;
}) {
  if (!name) return null;

  // ハンドルから線が出る向き。その延長線上に置けば、必ず自分のポートの隣になる
  const [dx, dy] = DIRECTION[position] ?? [0, 0];
  // 線と垂直な向き（進行方向から見た左）へずらして、線に重ねない
  const px = x + dx * GAP - dy * SIDE;
  const py = y + dy * GAP + dx * SIDE;

  const normalized = ((angle + 180) % 360) - 180;
  const flipped = normalized > 90 || normalized <= -90;
  // 上下逆さまの文字は読めないので、半回転させて向きを揃える
  const rotation = alongLine ? (flipped ? normalized + (normalized > 0 ? -180 : 180) : normalized) : 0;

  return (
    <div
      className="topo-edge-label port"
      style={{
        transform: `translate(-50%, -50%) translate(${px}px, ${py}px) rotate(${rotation}deg)`,
        color: MEDIA_COLOR[mediaOf(name)],
        ...(opacity ? { opacity } : {}),
      }}
      title={name}
    >
      {formatPort(name, style)}
    </div>
  );
}
