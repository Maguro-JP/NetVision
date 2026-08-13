import { afterEach, describe, expect, it, vi } from "vitest";

import { debounce } from "./debounce";

afterEach(() => {
  vi.useRealTimers();
});

describe("検索の間引き", () => {
  it("連続した呼び出しは最後だけ実行する", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const delayed = debounce(fn, 300);

    delayed("a");
    delayed("ab");
    delayed("abc");
    vi.advanceTimersByTime(299);
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("abc");
  });

  it("キャンセルしたら実行しない", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const delayed = debounce(fn, 300);
    delayed("x");
    delayed.cancel();
    vi.advanceTimersByTime(500);
    expect(fn).not.toHaveBeenCalled();
  });
});
