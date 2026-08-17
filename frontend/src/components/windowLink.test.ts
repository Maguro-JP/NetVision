/**
 * 別ウィンドウで開くときの印。
 *
 * 1台を見るためだけの窓にメニューを出しても、押す相手がいないし幅も取る。
 * 開くときに URL へ印を付け、開いた先の画面がそれを見てメニューを畳む。
 */

import { describe, expect, it } from "vitest";

import { asWindowUrl, deviceWindow, WINDOW_FLAG } from "./WindowLink";

describe("別ウィンドウの印", () => {
  it("印を付ける", () => {
    expect(asWindowUrl("/devices/12")).toBe(`/devices/12?${WINDOW_FLAG}=1`);
  });

  it("すでに条件が付いていても壊さない", () => {
    expect(asWindowUrl("/devices/12?tab=ports")).toBe(`/devices/12?tab=ports&${WINDOW_FLAG}=1`);
  });

  it("窓の名前は機器ごとに分かれる（並べて見比べられる）", () => {
    expect(deviceWindow(1)).not.toBe(deviceWindow(2));
  });
});
