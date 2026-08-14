import { describe, expect, it } from "vitest";

import {
  applyPositions,
  canRedo,
  canUndo,
  capturePositions,
  changedIds,
  isDirty,
  pushStep,
  redo,
  startHistory,
  undo,
} from "./topologyEditHistory";

describe("配置の編集過程", () => {
  it("始めた直後は戻せない・やり直せない・汚れていない", () => {
    const history = startHistory({ "1": { x: 0, y: 0 } });
    expect(canUndo(history)).toBe(false);
    expect(canRedo(history)).toBe(false);
    expect(isDirty(history)).toBe(false);
    expect(changedIds(history)).toEqual([]);
  });

  it("動かしたあとは一つ戻せて、戻すと汚れていない", () => {
    let history = startHistory({ "1": { x: 0, y: 0 }, "2": { x: 10, y: 0 } });
    history = pushStep(history, { "1": { x: 40, y: 80 }, "2": { x: 10, y: 0 } });
    expect(isDirty(history)).toBe(true);
    expect(changedIds(history)).toEqual(["1"]);

    const back = undo(history);
    expect(back).not.toBeNull();
    expect(isDirty(back!)).toBe(false);
    expect(back!.stack[back!.index]["1"]).toEqual({ x: 0, y: 0 });
  });

  it("戻したあとにやり直せ、途中から動かすとやり直し側は消える", () => {
    let history = startHistory({ "1": { x: 0, y: 0 } });
    history = pushStep(history, { "1": { x: 10, y: 0 } });
    history = pushStep(history, { "1": { x: 20, y: 0 } });
    history = undo(history)!;
    expect(canRedo(history)).toBe(true);

    history = pushStep(history, { "1": { x: 99, y: 0 } });
    expect(canRedo(history)).toBe(false);
    expect(history.stack).toHaveLength(3);
    expect(history.stack[history.index]["1"]).toEqual({ x: 99, y: 0 });
    // やり直し側は捨てたので、進めない
    expect(redo(history)).toBeNull();
  });

  it("同じ配置を足しても過程は増えない", () => {
    let history = startHistory({ "1": { x: 0, y: 0 } });
    history = pushStep(history, { "1": { x: 0, y: 0 } });
    expect(history.stack).toHaveLength(1);
  });

  it("機器の座標を写し取って当て直せる", () => {
    const nodes = [
      { id: "1", position: { x: 1, y: 2 }, label: "a" },
      { id: "2", position: { x: 3, y: 4 }, label: "b" },
    ];
    const map = capturePositions(nodes);
    expect(map).toEqual({ "1": { x: 1, y: 2 }, "2": { x: 3, y: 4 } });
    expect(applyPositions(nodes, { "1": { x: 9, y: 8 } })).toEqual([
      { id: "1", position: { x: 9, y: 8 }, label: "a" },
      { id: "2", position: { x: 3, y: 4 }, label: "b" },
    ]);
  });
});
