import type { MouseEvent, ReactNode } from "react";

/**
 * 別ウィンドウで開くリンク。
 *
 * トポロジー図のように「開いたまま置いておきたい」画面へは、いまのページを
 * 動かさずに別ウィンドウを出す。台帳を見ながら図を確かめる、という見方が
 * 一度きりの移動で途切れないようにする。
 *
 * 同じ名前のウィンドウを使い回すので、押すたびに増えない。
 * href は残してあるので、中クリックや Ctrl+クリックはブラウザに任せる。
 */
export interface WindowLinkProps {
  /** 開く先。アプリ内のパス */
  to: string;
  /** ウィンドウの名前。同じ名前なら使い回す */
  name: string;
  title?: string;
  className?: string;
  width?: number;
  height?: number;
  children: ReactNode;
}

export function openInWindow(to: string, name: string, width = 1400, height = 900): void {
  const w = Math.min(width, window.screen.availWidth);
  const h = Math.min(height, window.screen.availHeight);
  // 同じ名前のウィンドウがあればそれを使う。押すたびに窓が増えない
  const opened = window.open(to, name, `width=${w},height=${h}`);
  if (!opened) {
    // ポップアップを止めている環境では、せめて別タブで開く
    window.open(to, "_blank", "noopener,noreferrer");
    return;
  }
  opened.focus();
}

export default function WindowLink({
  to,
  name,
  title,
  className,
  width,
  height,
  children,
}: WindowLinkProps) {
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    // 修飾キー・中クリックはブラウザの流儀に任せる
    if (event.defaultPrevented) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (event.button !== 0) return;
    event.preventDefault();
    openInWindow(to, name, width, height);
  };

  return (
    <a href={to} onClick={onClick} title={title} className={className}>
      {children}
    </a>
  );
}
