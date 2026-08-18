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

  it("React Flow の既定の枠と背景を打ち消している", () => {
    const block = css.slice(css.indexOf(".react-flow__node-group {"));
    const rule = block.slice(0, block.indexOf("}"));
    expect(rule).toMatch(/background:\s*transparent/);
    expect(rule).toMatch(/border:\s*none/);
    expect(rule).toMatch(/padding:\s*0/);
  });
});
