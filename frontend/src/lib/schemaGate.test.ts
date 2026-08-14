import { describe, expect, it } from "vitest";

import { shouldRedirectToMigration } from "./schemaGate";

describe("表の更新ページへの案内", () => {
  it("差分があるときは専用ページへ送る", () => {
    expect(shouldRedirectToMigration("needs_apply", "/dashboard")).toBe(true);
  });

  it("すでに専用ページにいるときは送らない", () => {
    expect(shouldRedirectToMigration("needs_apply", "/migration")).toBe(false);
  });

  it("更新後の確認待ちは、画面を見て確かめてよい", () => {
    expect(shouldRedirectToMigration("needs_confirm", "/devices")).toBe(false);
  });
});
