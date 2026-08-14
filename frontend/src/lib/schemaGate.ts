import type { SchemaPhase } from "../api/types";

/** 表に差分があるときは、専用ページ以外へ進ませない。 */
export function shouldRedirectToMigration(phase: SchemaPhase | undefined, path: string): boolean {
  return phase === "needs_apply" && path !== "/migration";
}
