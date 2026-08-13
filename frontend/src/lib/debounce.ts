import { useEffect, useState } from "react";

/**
 * 連続した呼び出しを間引いて、最後の1回だけ実行する。
 * 探す欄で1文字ごとに問い合わせると、打っている途中の画面がチラつく。
 */
export function debounce<Args extends unknown[]>(
  fn: (...args: Args) => void,
  delayMs: number,
): ((...args: Args) => void) & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const wrapped = ((...args: Args) => {
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  }) as ((...args: Args) => void) & { cancel: () => void };
  wrapped.cancel = () => {
    if (timer !== undefined) clearTimeout(timer);
  };
  return wrapped;
}

/** 値が落ち着いてから返す。入力中の途中経過では問い合わせない。 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

export const SEARCH_DELAY_MS = 300;
