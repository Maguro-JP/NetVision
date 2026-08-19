import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * 枠は1つだけ描く。
 *
 * React Flow は枠の入れ物（.react-flow__node-group）にも既定で背景と枠線と
 * 余白を持たせる。打ち消さないと、こちらが描く枠の後ろに数 px ずれた四角が
 * 出て、背景と枠がずれているように見える。実際にそう見えていた。
 */
describe("囲み枠", () => {
  const css = readFileSync(
    fileURLToPath(new URL("../styles.css", import.meta.url)),
    "utf-8",
  );

  it("名前は枠の左上から 12px。書き出す SVG と同じ位置にする", () => {
    const block = css.slice(css.indexOf(".topo-group-label {"));
    const rule = block.slice(0, block.indexOf("}"));
    const left = (property: string): number => {
      const found = new RegExp(`${property}:\\s*([^;]+);`).exec(rule);
      expect(found).not.toBeNull();
      const values = found![1].trim().split(/\s+/);
      // 1つなら四辺共通、2つなら縦横、4つなら上右下左
      const value =
        values.length === 1
          ? values[0]
          : values.length === 2
            ? values[1]
            : values[3];
      return Number.parseInt(value, 10);
    };
    expect(left("margin") + left("padding")).toBe(12);
  });

  it("React Flow の既定の枠と背景を打ち消している", () => {
    const block = css.slice(css.indexOf(".react-flow__node-group {"));
    const rule = block.slice(0, block.indexOf("}"));
    expect(rule).toMatch(/background:\s*transparent/);
    expect(rule).toMatch(/border:\s*none/);
    expect(rule).toMatch(/padding:\s*0/);
  });
});
