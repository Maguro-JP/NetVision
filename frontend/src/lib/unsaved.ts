import { useEffect } from "react";

/**
 * 保存していない変更があるとき、タブを閉じる・更新する前に止める。
 * ブラウザ標準の確認が出る。文言はブラウザ側が決める。
 */
export function useUnsavedWarning(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}

export function confirmLeave(dirty: boolean): boolean {
  if (!dirty) return true;
  return window.confirm("保存していない変更があります。破棄して移動しますか？");
}
