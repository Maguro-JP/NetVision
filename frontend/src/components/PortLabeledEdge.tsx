import { BaseEdge, EdgeLabelRenderer, Position, type EdgeProps } from "@xyflow/react";

import { MEDIA_COLOR, formatPort, mediaOf, type PortStyle } from "../lib/portlabel";

/** 段が違うと見なす縦の差。これ未満なら横並びとして扱う。 */
type Face = "top" | "bottom" | "left" | "right";
type Pt = { x: number; y: number };

const SAME_ROW = 60;

/** 右の列（拠点の対向など）と見なす横の差。 */
const COLUMN = 400;

/** 「横にいる」と見なす比。縦の差のこの倍だけ横へ離れていれば横から出す。 */
const SIDEWAYS = 1.5;

/**
 * 相手がどちらにいるかで、線を出す辺を決める。
 *
 * 画面・線の引き回し・書き出しで同じ判断にする。別々に書いていたときは、
 * 画面だけが古い規則のままで、右にいる相手へ下から線を出していた。
 */
export function sideBetween(dx: number, dy: number): Face {
  if (Math.abs(dy) < SAME_ROW) return dx > 0 ? "right" : "left";
  if (Math.abs(dx) >= COLUMN) return dx > 0 ? "right" : "left";
  if (Math.abs(dx) >= SIDEWAYS * Math.abs(dy)) return dx > 0 ? "right" : "left";
  return dy > 0 ? "bottom" : "top";
}

export interface WireBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

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
  /** 隣の線が近すぎる端。ポート名を線の反対側（上下の辺なら1行外）へ逃がす */
  sourceFlip?: boolean;
  targetFlip?: boolean;
  /** 自分以外の機器の箱。線が貫かないよう迂回する */
  obstacles?: WireBox[];
  sourceBox?: WireBox;
  targetBox?: WireBox;
  /**
   * サーバーが引いた折れ線。画面はこれを描く。
   *
   * 線の引き方を画面と書き出しの両方に置くと、同じ規則で書いてあっても
   * 候補の選び方が少し違うだけで別の図になる。引くのは1か所だけにする。
   */
  points?: [number, number][];
  sourceSide?: "top" | "bottom" | "left" | "right";
  targetSide?: "top" | "bottom" | "left" | "right";
  /**
   * サーバーが決めたポート名の置き場所。線と同じく、決めるのは1か所にする。
   * 動かしている最中は線が仮なので、そのときだけ画面側の置き方に戻す。
   */
  sourceLabel?: PlacedLabel;
  targetLabel?: PlacedLabel;
}

/** サーバーが決めたポート名1つ。座標は図の座標。 */
export interface PlacedLabel {
  x: number;
  y: number;
  text: string;
  full: string;
  anchor: string;
  angle: number;
  color: string;
}

/** ノード枠から、その先どのくらい離してラベルを置くか。 */
export const PORT_LABEL_GAP = 14;
/** 線に重ならないよう、線と垂直な向きへずらす幅。束の本数でさらに開く。 */
export const PORT_LABEL_SIDE = 12;

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
  flip: boolean = false,
): { x: number; y: number; anchor: string } {
  const [dx, dy] = DIRECTION[position] ?? [0, 0];
  // 左右の辺は、どちらの辺から出ていても同じ向き（下）へずらす。
  // 辺ごとに向きを変えると、向かい合う機器のポート名が同じ高さに並んで重なる。
  // ただし隣の線が近すぎるときだけは、上へ逃がす（flip）。ブリッジのように
  // 2本を寄せて張るところは、下に揃えると必ず文字がぶつかる
  const across = dx !== 0 ? (flip ? -Math.abs(side) : Math.abs(side)) : dx * side;
  // 上下の辺は左右へ逃がせない（線に沿って読むため）。代わりに1行ぶん外へ
  // 出して、隣のポート名と段違いにする
  const rows = dx === 0 && flip ? gap + PORT_LABEL_SIDE : gap;
  const px = x + dx * gap - dy * side;
  const py = y + dy * rows + across;
  // 上下へ出すときは、線の左右どちらへ寄せたかに合わせて文字を伸ばす。
  // 中央寄せにすると、上から出た線と下から出た線のポート名が同じ幅を取り合い、
  // 段の間が狭いところで必ず重なる
  const anchor =
    position === Position.Right
      ? "0%, -50%"
      : position === Position.Left
        ? "-100%, -50%"
        : position === Position.Bottom
          ? "-100%, 0%"
          : "0%, -100%";
  return { x: px, y: py, anchor };
}
function pointsToPath(points: Pt[]): { path: string; labelX: number; labelY: number } {
  const cleaned: Pt[] = [points[0]];
  for (const point of points.slice(1)) {
    const last = cleaned[cleaned.length - 1];
    if (Math.abs(point.x - last.x) > 0.5 || Math.abs(point.y - last.y) > 0.5) {
      cleaned.push(point);
    }
  }
  const path = cleaned.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x},${p.y}`).join(" ");
  let bestLen = -1;
  let labelX = cleaned[0]?.x ?? 0;
  let labelY = cleaned[0]?.y ?? 0;
  for (let i = 0; i < cleaned.length - 1; i += 1) {
    const a = cleaned[i];
    const b = cleaned[i + 1];
    const len = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
    if (len > bestLen) {
      bestLen = len;
      labelX = (a.x + b.x) / 2;
      labelY = (a.y + b.y) / 2;
    }
  }
  return { path, labelX, labelY };
}

/** 箱の内側を通るか。辺上の接続は貫きにしない（輸出 SVG と同じ判定）。 */
export function segmentPiercesBox(box: WireBox, a: Pt, b: Pt): boolean {
  const pad = 4;
  const ix1 = box.x1 + pad;
  const iy1 = box.y1 + pad;
  const ix2 = box.x2 - pad;
  const iy2 = box.y2 - pad;
  if (ix2 <= ix1 || iy2 <= iy1) return false;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  let t0 = 0;
  let t1 = 1;
  const clips: Array<[number, number]> = [
    [-dx, a.x - ix1],
    [dx, ix2 - a.x],
    [-dy, a.y - iy1],
    [dy, iy2 - a.y],
  ];
  for (const [p, q] of clips) {
    if (Math.abs(p) < 1e-12) {
      if (q < 0) return false;
      continue;
    }
    const t = q / p;
    if (p < 0) t0 = Math.max(t0, t);
    else t1 = Math.min(t1, t);
    if (t0 > t1) return false;
  }
  return t1 - t0 > 1e-4 && t0 < 1 - 1e-9 && t1 > 1e-9;
}
/**
 * 箱を動かしている最中の、仮の線。
 *
 * 本物の線はサーバーが引く。動かしているあいだは口の位置が変わり続けるので、
 * その形は使えない。縦横に1回だけ曲げた線を出しておいて、手を離したら
 * 引き直してもらう。ここで凝った経路を作ると、線の引き方が2つに増える。
 */
export function previewRoute(
  from: Pt,
  to: Pt,
  face: Face,
): { path: string; labelX: number; labelY: number } {
  const horizontalFirst = face === "left" || face === "right";
  const corner = horizontalFirst ? { x: to.x, y: from.y } : { x: from.x, y: to.y };
  return pointsToPath([from, corner, to]);
}

function parsePathPoints(path: string): Pt[] {
  return path
    .split(/[ML]\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [x, y] = part.split(",").map(Number);
      return { x, y };
    });
}
export function facesForEdge(input: {
  sourceBox: WireBox;
  targetBox: WireBox;
  obstacles: WireBox[];
  sourceSide?: Face | null;
  targetSide?: Face | null;
}): { sourceFace: Face; targetFace: Face } {
  const mid = (box: WireBox) => ({
    x: (box.x1 + box.x2) / 2,
    y: (box.y1 + box.y2) / 2,
  });
  const srcMid = mid(input.sourceBox);
  const dstMid = mid(input.targetBox);
  const toward = (from: Pt, to: Pt): Face => sideBetween(to.x - from.x, to.y - from.y);
  const sourceFace = input.sourceSide ?? toward(srcMid, dstMid);
  const targetFace = input.targetSide ?? toward(dstMid, srcMid);
  // ここは、サーバーから線が届かなかったときの控えでしかない。凝った
  // 選び方をすると、線の引き方が画面にもある状態に戻ってしまう
  return { sourceFace, targetFace };
}

const FACE_OF: Record<Position, "top" | "bottom" | "left" | "right"> = {
  [Position.Top]: "top",
  [Position.Bottom]: "bottom",
  [Position.Left]: "left",
  [Position.Right]: "right",
};

/**
 * 画面が実際に引いた線。書き出しはここから取る。
 *
 * 書き出しが自分でもう一度線を引くと、同じ規則で書いてあっても候補の
 * 選び方が少し違うだけで別の図になる。人は画面で確かめてから「出力する」を
 * 押すのだから、そこで違う図が出てはいけない。
 *
 * 画面から消えた線は残っても構わない。読む側は、いま出ている結線の分だけを
 * 引くようにしてある。
 */
export const drawnWires = new Map<
  string,
  { points: Pt[]; sourceFace: Face; targetFace: Face }
>();

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
  // サーバーが引いた線。線の引き方は1か所に置いてあり、画面はそれを描く。
  // 箱を動かしている最中は口の位置が変わるので、そのあいだだけ仮の線にする
  const given = info.points;
  const fits =
    given !== undefined &&
    given.length >= 2 &&
    Math.abs(given[0][0] - sourceX) < 12 &&
    Math.abs(given[0][1] - sourceY) < 12 &&
    Math.abs(given[given.length - 1][0] - targetX) < 12 &&
    Math.abs(given[given.length - 1][1] - targetY) < 12;
  const routed = fits
    ? pointsToPath(given!.map(([x, y]) => ({ x, y })))
    : previewRoute(
        { x: sourceX, y: sourceY },
        { x: targetX, y: targetY },
        FACE_OF[sourcePosition],
      );
  const path = routed.path;
  const labelX = routed.labelX;
  const labelY = routed.labelY;
  // 引いた線を控える。書き出しはこれをそのまま描く
  drawnWires.set(id, {
    points: parsePathPoints(path),
    sourceFace: FACE_OF[sourcePosition],
    targetFace: FACE_OF[targetPosition],
  });
  const angle = (Math.atan2(targetY - sourceY, targetX - sourceX) * 180) / Math.PI;
  // 左右の辺どうしを結ぶ線では、両端のポート名が箱の間の通路を分け合う。
  // 半分を超えると必ず出会うので、そこで切って吹き出しで読ませる
  const corridor =
    sourcePosition === Position.Left || sourcePosition === Position.Right
      ? Math.abs(targetX - sourceX)
      : 0;
  // 通路が狭いときは、箱から離す量も詰める。離したままだと名前が置けない
  const sideGap = corridor && corridor < 160 ? 8 : PORT_LABEL_GAP;
  // 通路の半分から、箱との間だけを引く。文字の余白は箱の中に含まれる
  const sideRoom = corridor ? Math.max(24, corridor / 2 - sideGap - 2) : undefined;
  // 警告は線のまん中に、印だけ置く。ポート名は線の左右へ出してあるので、
  // 線の上そのものが空いている。文字まで出すと必ずどちらかのポート名に乗る

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {!info.hideLabels && (
        <EdgeLabelRenderer>
          {/* 人が付けた線の名前は中央に置く。両端のポート名はそのまま残し、
              上（相手のポート）・中央（名前）・下（自分のポート）が縦に並ぶ */}
          {info.manualLabel && (
            <div
              className="topo-edge-label center"
              style={{
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
            >
              {info.manualLabel}
            </div>
          )}
          {fits && info.sourceLabel ? (
            <PlacedPortLabel placed={info.sourceLabel} opacity={style?.opacity} />
          ) : (
          <PortLabel
            name={info.sourceInterface ?? ""}
            flip={info.sourceFlip ?? false}
            x={sourceX}
            y={sourceY}
            position={sourcePosition}
            maxWidth={sideRoom}
            gap={sideGap}
            angle={angle}
            style={info.portStyle ?? "short"}
            alongLine={info.alongLine ?? false}
            opacity={style?.opacity}
            color={typeof style?.stroke === "string" ? style.stroke : undefined}
          />
          )}
          {fits && info.targetLabel ? (
            <PlacedPortLabel placed={info.targetLabel} opacity={style?.opacity} />
          ) : (
          <PortLabel
            name={info.targetInterface ?? ""}
            flip={info.targetFlip ?? false}
            x={targetX}
            y={targetY}
            position={targetPosition}
            maxWidth={sideRoom}
            gap={sideGap}
            // 相手側から見た向き。ずらす向きが反転して、2つのラベルが
            // 線の反対側に出るので、機器が近くても重ならない
            angle={angle + 180}
            style={info.portStyle ?? "short"}
            alongLine={info.alongLine ?? false}
            opacity={style?.opacity}
            color={typeof style?.stroke === "string" ? style.stroke : undefined}
          />
          )}
          {info.warning && (
            <div
              className="topo-edge-label warning"
              style={{
                // 警告は線のまん中に置く。ポート名は線の左右へ出してあるので、
                // 線の上そのものが空いている
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                ...(style?.opacity ? { opacity: style.opacity } : {}),
              }}
              title={info.warning}
            >
              {/* 文字まで出すと必ずどこかのポート名に乗る。印だけ出し、
                  中身は線を選んだときのパネルとここの吹き出しで読む */}
              ⚠
            </div>
          )}
        </EdgeLabelRenderer>
      )}
    </>
  );
}

/**
 * 置ける幅に収まる書き方を選ぶ。
 *
 * 箱の間が狭いところでは、略記でも収まらず「Te…」のように切れてしまう。
 * それなら番号だけ（1/1/1）の方が、どのポートかを言い当てられる。
 */
function shortEnough(name: string, style: PortStyle, maxWidth?: number): string {
  const text = formatPort(name, style);
  if (!maxWidth) return text;
  // 10px の文字は1文字あたり約6px。左右の余白 4px ずつを差し引いて測る
  if (text.length * 6 <= maxWidth - 8) return text;
  return formatPort(name, "number");
}

/** SVG の baseline から、文字の入った箱の上端までのおよその距離（px）。 */
const BASELINE_TO_TOP = 10.6;

/**
 * サーバーが決めた場所へポート名を置く。
 *
 * 置き場所を画面と書き出しの両方で決めると、同じ規則で書いてあっても
 * 結果が食い違う。線と同じで、決めるのは1か所にして画面はそれを描く。
 */
function PlacedPortLabel({
  placed,
  opacity,
}: {
  placed: PlacedLabel;
  opacity?: number | string;
}) {
  if (!placed.text) return null;
  // SVG の text-anchor を、箱の寄せ方に読み替える。左右の余白（padding）も戻す
  const shift =
    placed.anchor === "middle" ? "-50%" : placed.anchor === "end" ? "-100%" : "0%";
  const x = placed.anchor === "middle" ? placed.x : placed.anchor === "end" ? placed.x + 4 : placed.x - 4;
  return (
    <div
      className="topo-edge-label port"
      style={{
        transform:
          `translate(${shift}, 0) translate(${x}px, ${placed.y - BASELINE_TO_TOP}px)` +
          (placed.angle ? ` rotate(${placed.angle}deg)` : ""),
        color: placed.color,
        ...(opacity ? { opacity } : {}),
      }}
      title={placed.full}
    >
      {placed.text}
    </div>
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
  maxWidth,
  gap = PORT_LABEL_GAP,
  flip = false,
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
  /** 置ける幅の上限。狭い通路では名前を切って重なりを防ぐ */
  maxWidth?: number;
  /** 箱から離す量。狭い通路では詰める */
  gap?: number;
  /** 隣の線が近いときの逃がし */
  flip?: boolean;
}) {
  if (!name) return null;

  // 引き出し口そのものがポート名の行の高さぶん離してあるので、ラベルは
  // 線の同じ側へ同じ量だけ出す。1本ごとに上下へ振り分けると、同じ辺の
  // ポート名が互い違いになって、上から順に読めなくなる
  // 口そのものがポート名の行の高さぶん離してあるので、線に沿って押し出す必要は
  // ない。押し出すと、短い線では両端のポート名が中央で出会って重なる
  const placed = portLabelPlacement(x, y, position, gap, PORT_LABEL_SIDE, flip);
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
        ...(maxWidth ? { maxWidth } : {}),
        ...(opacity ? { opacity } : {}),
      }}
      title={name}
    >
      {shortEnough(name, style, maxWidth)}
    </div>
  );
}
