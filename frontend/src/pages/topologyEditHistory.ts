/**
 * トポロジ配置の編集過程。
 *
 * 保存前と保存後だけでは足りない。ドラッグのたびに途中の状態を残し、
 * 「一つ戻す」「やり直し」で行き来できるようにする。破棄は編集開始時
 * （または最後の保存直後）へ戻す。
 */

export type Position = { x: number; y: number };
export type PositionMap = Record<string, Position>;

export type EditHistory = {
  /** 編集を始めたときの配置。破棄でここへ戻る */
  baseline: PositionMap;
  /** 過程。先頭は baseline と同じ */
  stack: PositionMap[];
  /** いま見ている過程の位置 */
  index: number;
};

export function capturePositions(
  nodes: { id: string; position: Position }[],
): PositionMap {
  const out: PositionMap = {};
  for (const node of nodes) {
    // 囲み枠の位置は機器から計算するので、過程には載せない
    if (node.id.startsWith("g-")) continue;
    out[node.id] = { x: node.position.x, y: node.position.y };
  }
  return out;
}

export function startHistory(positions: PositionMap): EditHistory {
  const snapshot = cloneMap(positions);
  return { baseline: snapshot, stack: [cloneMap(snapshot)], index: 0 };
}

/** 編集の途中で箱が増えたとき、過程の全部に今の位置を足す。足さないと戻せない。 */
export function absorbPositions(history: EditHistory, positions: PositionMap): EditHistory {
  const baseline = cloneMap(history.baseline);
  const stack = history.stack.map(cloneMap);
  let changed = false;
  for (const [id, position] of Object.entries(positions)) {
    if (id in baseline) continue;
    changed = true;
    baseline[id] = { ...position };
    for (const frame of stack) {
      if (!(id in frame)) frame[id] = { ...position };
    }
  }
  return changed ? { ...history, baseline, stack } : history;
}

/** いまの配置を過程に足す。同じ内容なら何もしない。やり直し側は捨てる。 */
export function pushStep(history: EditHistory, positions: PositionMap): EditHistory {
  const current = history.stack[history.index];
  if (sameMap(current, positions)) return history;
  const stack = history.stack.slice(0, history.index + 1);
  stack.push(cloneMap(positions));
  return { ...history, stack, index: stack.length - 1 };
}

export function undo(history: EditHistory): EditHistory | null {
  if (history.index <= 0) return null;
  return { ...history, index: history.index - 1 };
}

export function redo(history: EditHistory): EditHistory | null {
  if (history.index >= history.stack.length - 1) return null;
  return { ...history, index: history.index + 1 };
}

export function canUndo(history: EditHistory): boolean {
  return history.index > 0;
}

export function canRedo(history: EditHistory): boolean {
  return history.index < history.stack.length - 1;
}

/** 過程が編集開始時から動いているか */
export function isDirty(history: EditHistory): boolean {
  return history.index > 0;
}

export function currentPositions(history: EditHistory): PositionMap {
  return history.stack[history.index];
}

/** 保存対象。基準と違う機器だけ */
export function changedIds(history: EditHistory): string[] {
  const now = history.stack[history.index];
  const ids = new Set([...Object.keys(history.baseline), ...Object.keys(now)]);
  const out: string[] = [];
  for (const id of ids) {
    const a = history.baseline[id];
    const b = now[id];
    if (!a || !b || a.x !== b.x || a.y !== b.y) out.push(id);
  }
  return out;
}

export function applyPositions<T extends { id: string; position: Position }>(
  nodes: T[],
  positions: PositionMap,
): T[] {
  return nodes.map((node) => {
    const position = positions[node.id];
    return position ? { ...node, position: { ...position } } : node;
  });
}

function cloneMap(positions: PositionMap): PositionMap {
  const out: PositionMap = {};
  for (const [id, position] of Object.entries(positions)) {
    out[id] = { x: position.x, y: position.y };
  }
  return out;
}

function sameMap(a: PositionMap, b: PositionMap): boolean {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  for (const id of keys) {
    const other = b[id];
    if (!other || other.x !== a[id].x || other.y !== a[id].y) return false;
  }
  return true;
}
