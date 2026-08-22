/**
 * 裏で走らせた処理の見え方。
 *
 * 取り込みを裏に回したので、頼んだあと画面を離れられる。離れた人が
 * 「あれはどうなったのか」を追えるように、進み具合と結果を一文にする。
 */

import { describe, expect, it } from "vitest";

import type { Job } from "../api/types";
import {
  outcomeText,
  pollInterval,
  progressText,
  statusLabel,
} from "./JobPanel";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "abc",
    kind: "device-import",
    kind_label: "機器台帳の取り込み",
    label: "daichou.csv",
    status: "running",
    seconds: 3,
    started_at: null,
    done: 0,
    total: null,
    percent: null,
    note: null,
    error: null,
    draft: null,
    device_import: null,
    link_import: null,
    ...overrides,
  } as Job;
}

describe("聞きに行く間隔", () => {
  it("走っているものがあるあいだは、こまめに聞く", () => {
    expect(pollInterval([job()])).toBe(1000);
  });

  it("全部終わっていれば、聞きに行かない", () => {
    expect(pollInterval([job({ status: "done" })])).toBe(false);
  });

  it("そもそも何も無ければ、聞きに行かない", () => {
    expect(pollInterval([])).toBe(false);
    expect(pollInterval(undefined)).toBe(false);
  });
});

describe("進み具合の一文", () => {
  it("総数が分かるなら、何行目まで来たかを出す", () => {
    const text = progressText(
      job({
        done: 80000,
        total: 160000,
        percent: 50,
        note: "1行ずつ見ています",
      }),
    );
    expect(text).toBe("1行ずつ見ています — 80,000 / 160,000 行（50%）");
  });

  it("総数が分からないうちは、いま何をしているかだけ出す", () => {
    expect(progressText(job({ note: "読み込んでいます" }))).toBe(
      "読み込んでいます",
    );
  });

  it("終わったものには進み具合を出さない", () => {
    expect(progressText(job({ status: "done" }))).toBe("");
  });
});

describe("終わったあとの一文", () => {
  it("取り込みは、何件入ったのかまで出す", () => {
    const text = outcomeText(
      job({
        status: "done",
        device_import: {
          created: 12,
          updated: 3,
          errors: 1,
        } as Job["device_import"],
      }),
    );
    expect(text).toBe("新規 12 件 / 更新 3 件 / 問題のあった行 1 件");
  });

  it("結線表は、本数で出す", () => {
    const text = outcomeText(
      job({
        kind: "link-import",
        status: "done",
        link_import: {
          linked: 5,
          already: 2,
          pending: 1,
        } as Job["link_import"],
      }),
    );
    expect(text).toBe("結線 5 本 / 既にあった 2 本 / 機器待ち 1 本");
  });

  it("失敗は理由をそのまま出す", () => {
    expect(
      outcomeText(job({ status: "error", error: "読めませんでした" })),
    ).toBe("失敗しました: 読めませんでした");
  });

  it("やめたものは、そう分かるようにする", () => {
    expect(outcomeText(job({ status: "cancelled" }))).toBe("途中でやめました");
    expect(statusLabel(job({ status: "cancelled" }))).toBe("中止");
  });
});
