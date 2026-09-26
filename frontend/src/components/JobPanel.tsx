import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "../api/client";
import type { Job } from "../api/types";

/**
 * 裏で走らせた仕事の様子。
 *
 * 取り込みは裏に回したので、頼んだあと画面を離れられる。離れられる代わりに
 * 「あれはどうなったのか」が分からなくなるので、いつも開くダッシュボードに
 * まとめて出す。
 */

const RUNNING = new Set(["pending", "running"]);

/** 走っているものがあるあいだだけ、こまめに聞く。無ければ聞きに行かない。 */
export function pollInterval(jobs: Job[] | undefined): number | false {
  if (!jobs || jobs.length === 0) return false;
  return jobs.some((job) => RUNNING.has(job.status)) ? 1000 : false;
}

/** 進み具合の一文。総数が分からないうちは、いま何をしているかだけ出す。 */
export function progressText(job: Job): string {
  if (!RUNNING.has(job.status)) return "";
  const note = job.note ?? "受け付けました";
  if (!job.total) return note;
  const counted = `${job.done.toLocaleString()} / ${job.total.toLocaleString()} 行`;
  return job.percent === null
    ? `${note} — ${counted}`
    : `${note} — ${counted}（${job.percent}%）`;
}

/** 終わった仕事の一言。何件入ったのかまで出す。 */
export function outcomeText(job: Job): string {
  if (job.status === "error")
    return `失敗しました: ${job.error ?? "理由は残っていません"}`;
  if (job.status === "cancelled") return "途中でやめました";
  if (job.status !== "done") return "";
  if (job.device_import) {
    const r = job.device_import;
    // 何も変わらなかった行は、あるときだけ言う（書き出した台帳を戻したときに多い）
    const same = r.unchanged ? ` / 変更なし ${r.unchanged} 件` : "";
    return `新規 ${r.created} 件 / 更新 ${r.updated} 件${same} / 問題のあった行 ${r.errors} 件`;
  }
  if (job.link_import) {
    const r = job.link_import;
    return `結線 ${r.linked} 本 / 既にあった ${r.already} 本 / 機器待ち ${r.pending} 本`;
  }
  return "終わりました";
}

export function statusLabel(job: Job): string {
  if (RUNNING.has(job.status)) return "進行中";
  if (job.status === "done") return "完了";
  if (job.status === "error") return "失敗";
  return "中止";
}

export default function JobPanel() {
  const queryClient = useQueryClient();
  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs(10),
    refetchInterval: (query) => pollInterval(query.state.data),
  });

  // 取り込みが終わったら、台帳の数え直しを促す。裏で入ったぶんが
  // 画面に出ないままだと、入っていないように見える
  const running = useRef(0);
  useEffect(() => {
    const now = (jobs ?? []).filter((job) => RUNNING.has(job.status)).length;
    if (running.current > 0 && now === 0) {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
      queryClient.invalidateQueries({ queryKey: ["topology"] });
    }
    running.current = now;
  }, [jobs, queryClient]);

  if (!jobs || jobs.length === 0) return null;

  return (
    <>
      <h2>裏で走らせた処理</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        覚えているのは動いているあいだだけです。NetVision
        を起動し直すと、この一覧は空に戻ります （台帳に入ったぶんは残ります）。
      </p>
      <table>
        <thead>
          <tr>
            <th>内容</th>
            <th>状態</th>
            <th>経過</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                {job.kind_label}
                {job.label ? `: ${job.label}` : ""}
              </td>
              <td>
                {statusLabel(job)}
                {RUNNING.has(job.status) && (
                  <button
                    style={{ marginLeft: 8 }}
                    onClick={() => {
                      api
                        .cancelJob(job.id)
                        .then(() =>
                          queryClient.invalidateQueries({ queryKey: ["jobs"] }),
                        );
                    }}
                  >
                    やめる
                  </button>
                )}
              </td>
              <td>{Math.round(job.seconds)} 秒</td>
              <td>
                {RUNNING.has(job.status) ? progressText(job) : outcomeText(job)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
