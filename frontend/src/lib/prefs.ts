/**
 * 画面の好み。
 *
 * 人によって作業の仕方が違うところは、こちらで決めきらずに選べるようにする。
 * 台帳を1つずつ追う人はこのページのまま、図と突き合わせる人は別ウィンドウ、
 * というだけの違いで、どちらが正しいということはない。
 *
 * 置き場所はブラウザ（localStorage）。閉域で1台ずつ使う道具なので、
 * サーバに持たせて同期する必要がない。
 */

import { useEffect, useState } from "react";

/** 機器などを開くときの動き */
export type OpenTarget = "same" | "tab" | "window";

export const OPEN_TARGET_LABEL: Record<OpenTarget, string> = {
  same: "このページで開く",
  tab: "別のタブで開く",
  window: "別のウィンドウで開く",
};

export interface Prefs {
  /** 一覧や図から機器を開くときの動き */
  openTarget: OpenTarget;
}

export const DEFAULT_PREFS: Prefs = {
  // 図や一覧を見ながら1台ずつ確かめる使い方が多いので、既定は別ウィンドウ。
  // 元の画面（絞り込みや配置）を保ったまま行き来できる
  openTarget: "window",
};

const KEY = "netvision.prefs";
/** 同じタブの中の別の画面へも変更を伝える */
const EVENT = "netvision-prefs";

export function readPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_PREFS;
    const saved = JSON.parse(raw) as Partial<Prefs>;
    return { ...DEFAULT_PREFS, ...saved };
  } catch {
    // 壊れていたら既定に戻す。設定のために画面が出ないのは本末転倒
    return DEFAULT_PREFS;
  }
}

export function writePrefs(next: Partial<Prefs>): Prefs {
  const merged = { ...readPrefs(), ...next };
  localStorage.setItem(KEY, JSON.stringify(merged));
  window.dispatchEvent(new CustomEvent(EVENT));
  return merged;
}

/** いまの好み。他の画面（別タブ・別ウィンドウ）で変えても追いつく。 */
export function usePrefs(): [Prefs, (next: Partial<Prefs>) => void] {
  const [prefs, setPrefs] = useState<Prefs>(readPrefs);

  useEffect(() => {
    const sync = () => setPrefs(readPrefs());
    window.addEventListener(EVENT, sync);
    // 別のタブ・ウィンドウで変えたときは storage イベントで届く
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return [prefs, (next: Partial<Prefs>) => setPrefs(writePrefs(next))];
}
