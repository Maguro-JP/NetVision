import type { MouseEvent, ReactNode } from "react";
import { Link } from "react-router-dom";

import { readPrefs, usePrefs, type OpenTarget } from "../lib/prefs";

/**
 * 機器などを開くリンク。開き方は設定に従う。
 *
 * 図や一覧を見ながら1台ずつ確かめるとき、開くたびに元の画面が消えると、
 * 絞り込みや図の配置をやり直すことになる。別ウィンドウ（または別タブ）で
 * 開けば、元の画面はそのまま残る。
 *
 * どれが良いかは人と作業による（設定 → 機器を開くとき）ので、選べるようにする。
 * 窓には名前を付けてあるので、何度押しても増えない。
 * href は残してあるので、中クリックや Ctrl+クリックはブラウザに任せる。
 */
/**
 * 機器情報を出すウィンドウの名前。機器ごとに別の窓にする。
 *
 * 名前を1つにすると2台目で1台目が置き換わり、並べて見比べられない。
 * 機器ごとに分ければ、同じ機器をもう一度押したときだけ、その窓が前に出る。
 */
export function deviceWindow(deviceId: number | string): string {
  return `netvision-device-${deviceId}`;
}

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

/** 設定に従って開く。図の中のダブルクリックなど、リンク以外からも使う。 */
export function openInWindow(to: string, name: string, width = 1400, height = 900): void {
  const target: OpenTarget = readPrefs().openTarget;
  if (target === "same") {
    window.location.assign(to);
    return;
  }
  if (target === "tab") {
    window.open(to, "_blank", "noopener,noreferrer");
    return;
  }
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
  const [prefs] = usePrefs();

  // このページで開く設定なら、ふつうの画面遷移（読み込み直さない）
  if (prefs.openTarget === "same") {
    return (
      <Link to={to} title={title} className={className}>
        {children}
      </Link>
    );
  }

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
