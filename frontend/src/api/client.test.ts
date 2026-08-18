import { describe, expect, it } from "vitest";

import { ApiError, humanizeHttpError, isRetryableError } from "../api/client";

describe("エラーの見せ方", () => {
  it("見つからないときは、HTTP の数字ではなく次の一手が読める", () => {
    expect(humanizeHttpError(404, "404 Not Found")).toMatch(/見つかりませんでした/);
  });

  it("ファイル过大は分けて取り込む案内にする", () => {
    expect(humanizeHttpError(413, "413")).toMatch(/大きすぎ/);
  });

  it("サーバー障害は待ってやり直す案内にする", () => {
    expect(humanizeHttpError(500, "500")).toMatch(/しばらく/);
  });

  it("入力ミス（4xx）は同じ内容を送り直さない", () => {
    expect(isRetryableError(new ApiError(422, "hostname: required"))).toBe(false);
    expect(isRetryableError(new ApiError(404, "missing"))).toBe(false);
  });

  it("通信切れとサーバー障害はもう一度試せる", () => {
    expect(isRetryableError(new ApiError(0, "timeout"))).toBe(true);
    expect(isRetryableError(new ApiError(503, "busy"))).toBe(true);
  });
});
