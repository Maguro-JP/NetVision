import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { TAPS_TO_OPEN, opensAt } from "./HiddenPage";
import type { AppSettings } from "../api/types";
import {
  DEFAULT_PREFS,
  OPEN_TARGET_LABEL,
  usePrefs,
  type OpenTarget,
} from "../lib/prefs";

/**
 * 画面の好みを決めるところ。
 *
 * 人によって作業の仕方が違うところだけを置く。台帳の中身や判定のしきい値は
 * 運用そのものに効くので、ここではなく環境変数と台帳で決める。
 *
 * 好みはこのブラウザに残る。閉域で1台ずつ使う道具なので、サーバーに持たせて
 * 同期する必要がない。別の端末では別に設定する。
 */
export default function SettingsPage() {
  const [prefs, setPrefs] = usePrefs();
  const queryClient = useQueryClient();
  const { data: llm } = useQuery({
    queryKey: ["llmStatus"],
    queryFn: () => api.llmStatus(),
  });
  // 使うかどうかは、その場で切り替えられるようにする。環境変数だと起動し直しになり、
  // 打ち間違いも起きる
  const { data: app } = useQuery({
    queryKey: ["appSettings"],
    queryFn: () => api.appSettings(),
  });
  // 決めた値はその場で効く。起動し直しを求めない
  const saveApp = useMutation({
    mutationFn: (body: Partial<AppSettings>) => api.updateAppSettings(body),
    onSuccess: (next) => {
      queryClient.setQueryData(["appSettings"], next);
      for (const key of [
        "devices",
        "summary",
        "alerts",
        "referenceFreshness",
        "llmStatus",
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  const switchLlm = useMutation({
    mutationFn: (enabled: boolean) => api.setLlmEnabled(enabled),
    onSuccess: (status) => queryClient.setQueryData(["llmStatus"], status),
  });

  return (
    <div>
      <h1>設定</h1>
      <p className="subtitle">
        この画面の好みを決めます。設定はこのブラウザに残ります（別の端末には移りません）。
      </p>

      <div className="card settings-card">
        <h2>機器を開くとき</h2>
        <p className="muted">
          機器一覧やトポロジー図から機器情報を開いたときの動きです。図や絞り込みを
          残したまま見たいときは、別のウィンドウかタブを選んでください。
          トポロジー図では、機器をダブルクリックしても同じように開きます。
        </p>
        <div className="settings-choices">
          {(Object.keys(OPEN_TARGET_LABEL) as OpenTarget[]).map((value) => (
            <label key={value} className="settings-choice">
              <input
                type="radio"
                name="open-target"
                value={value}
                checked={prefs.openTarget === value}
                onChange={() => setPrefs({ openTarget: value })}
              />
              <span>{OPEN_TARGET_LABEL[value]}</span>
              <span className="muted">{OPEN_TARGET_NOTE[value]}</span>
            </label>
          ))}
        </div>
        <p className="muted">
          どの設定でも、Ctrl（Mac は ⌘）を押しながら、または中クリックで開けば、
          ブラウザのいつもどおりの動きになります。
        </p>
        {prefs.openTarget !== DEFAULT_PREFS.openTarget && (
          <div className="actions">
            <button
              onClick={() => setPrefs({ openTarget: DEFAULT_PREFS.openTarget })}
            >
              既定（{OPEN_TARGET_LABEL[DEFAULT_PREFS.openTarget]}）に戻す
            </button>
          </div>
        )}
      </div>

      {/* 運用しながら変えるものは画面から決める。環境変数を触らせると、
          打ち間違いが起きるし、そこで詰まる人も出る */}
      <div className="card settings-card">
        <h2>期限の見せ方と、時刻の地域</h2>
        <p className="muted">
          決めた値はその場で効きます（起動し直しは要りません）。この端末だけでなく、
          同じ NetVision を見ている全員に効きます。
        </p>
        {app === undefined ? (
          <p className="muted">読み込み中…</p>
        ) : (
          <div className="form-grid">
            <div className="field">
              <label htmlFor="warning-days">「要計画」にする日数</label>
              <input
                id="warning-days"
                type="number"
                min={1}
                max={3650}
                defaultValue={app.warning_days}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (value !== app.warning_days)
                    saveApp.mutate({ warning_days: value });
                }}
              />
              <span className="muted">
                保守期限・EoL がこの日数以内なら「要計画」
              </span>
            </div>
            <div className="field">
              <label htmlFor="critical-days">「要対応」にする日数</label>
              <input
                id="critical-days"
                type="number"
                min={1}
                max={3650}
                defaultValue={app.critical_days}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (value !== app.critical_days)
                    saveApp.mutate({ critical_days: value });
                }}
              />
              <span className="muted">
                「要計画」より短い日数にしてください
              </span>
            </div>
            <div className="field">
              <label htmlFor="stale-days">リファレンスが古いと見なす日数</label>
              <input
                id="stale-days"
                type="number"
                min={1}
                max={3650}
                defaultValue={app.reference_stale_days}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (value !== app.reference_stale_days)
                    saveApp.mutate({ reference_stale_days: value });
                }}
              />
              <span className="muted">
                閉域では自動で更新されないので、鮮度を出します
              </span>
            </div>
            <div className="field">
              <label htmlFor="timezone">時刻の地域</label>
              <input
                id="timezone"
                defaultValue={app.display_timezone}
                onBlur={(e) => {
                  if (e.target.value !== app.display_timezone)
                    saveApp.mutate({ display_timezone: e.target.value });
                }}
              />
              <span className="muted">
                画面と書き出しに出す時刻（例: Asia/Tokyo）
              </span>
            </div>
            <div className="field">
              <label htmlFor="model-dir">モデルの置き場所</label>
              <input
                id="model-dir"
                defaultValue={app.model_dir}
                onBlur={(e) => {
                  if (e.target.value !== app.model_dir)
                    saveApp.mutate({ model_dir: e.target.value });
                }}
              />
              <span className="muted">
                ここに .gguf を置くと、下書きに使います
              </span>
            </div>
          </div>
        )}
        {saveApp.error && (
          <div className="error">{(saveApp.error as Error).message}</div>
        )}
        {saveApp.isSuccess && <p className="ok">保存しました。</p>}
      </div>

      <div className="card settings-card">
        <h2>資料を読ませるモデル</h2>
        <p className="muted">
          手元か社内に立てたモデルがあると、取り込んだベンダー資料を根拠に
          Config の
          下書きを作れます。置いていなくても、資料から拾ったコマンドまでは出ます。
          外の事業者へは、明示して許すまで一切送りません。設定の仕方は
          docs/LOCAL_LLM.md にあります。
        </p>
        {llm && (
          <div className="actions">
            <button
              className={llm.enabled ? "" : "primary"}
              onClick={() => switchLlm.mutate(!llm.enabled)}
              disabled={switchLlm.isPending}
            >
              {llm.enabled ? "モデルを使わない" : "モデルを使う"}
            </button>
            <span className="muted">
              {llm.enabled
                ? "いまは使う設定です。置いてあれば下書きに使います"
                : "いまは使わない設定です。規則ベースだけで動きます"}
            </span>
          </div>
        )}
        {switchLlm.error && (
          <div className="error">{(switchLlm.error as Error).message}</div>
        )}
        {llm === undefined ? (
          <p className="muted">確認しています…</p>
        ) : llm.models.length === 0 ? (
          <p className="muted">
            モデルは設定されていません（NETVISION_LLM_URL /
            NETVISION_LLM_MODEL）。
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>役割</th>
                <th>モデル</th>
                <th>宛先</th>
                <th>状態</th>
              </tr>
            </thead>
            <tbody>
              {llm.models.map((model) => (
                <tr key={`${model.role}-${model.model}`}>
                  <td>
                    {model.role === "deep" ? "下書き・レビュー" : "要約・分類"}
                  </td>
                  <td className="mono">{model.model}</td>
                  <td className="mono">{model.endpoint}</td>
                  <td>
                    {model.usable ? (
                      <span className="badge ok">使えます</span>
                    ) : model.blocked_external ? (
                      <span className="badge expired">
                        外向きのため止めています
                      </span>
                    ) : (
                      <span className="badge" title={model.note}>
                        {model.note || "設定が足りません"}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card settings-card">
        <h2>ここにない設定</h2>
        <ul className="muted">
          <li>
            保守期限・EoL
            の警告日数、待ち受けるアドレスなどは、運用そのものに効くので
            環境変数で決めます（README に一覧があります）。
          </li>
          <li>
            トポロジー図の見せ方（シンプル表示・ポート名・並び）は、図の画面で選んだものが
            そのまま次回に残ります。
          </li>
        </ul>
      </div>

      <HiddenDoor />
    </div>
  );
}

/**
 * 表に出していない場所への入口。
 *
 * 途中のものをメニューに並べると、使えるものとして本番の判断に使われる。
 * かといって入口が無いと、置いたことごと忘れる。だから一番下の余白に、
 * 何度か触れば開く場所だけを置いておく。
 */
function HiddenDoor() {
  const [taps, setTaps] = useState(0);
  const opened = opensAt(taps);

  if (opened) {
    return (
      <div className="card settings-card" style={{ opacity: 0.85 }}>
        <h2>表に出していない場所</h2>
        <p className="muted">
          メニューには置いていないものです。作業用の画面ではないので、本番の判断には
          使わないでください。
        </p>
        <ul>
          <li>
            <Link to="/space">立体で眺める</Link>
            <div className="muted">
              拠点ごとのまとまりを立体で見ます。全体の形をつかむためのもので、
              流れている光は実際の通信量ではありません。
            </div>
          </li>
          <li>
            <Link to="/hidden">途中のもの</Link>
            <div className="muted">
              まだ確かめきれていない機能と、いまの版の情報です。
            </div>
          </li>
        </ul>
      </div>
    );
  }

  return (
    <div
      aria-hidden
      onClick={() => setTaps(taps + 1)}
      style={{
        cursor: "default",
        height: 48,
        opacity: taps === 0 ? 0 : 0.35,
        paddingTop: 16,
        textAlign: "center",
        userSelect: "none",
      }}
      title=""
    >
      <span className="muted" style={{ fontSize: 12 }}>
        {taps > 0 ? "・".repeat(Math.min(taps, TAPS_TO_OPEN)) : ""}
      </span>
    </div>
  );
}

const OPEN_TARGET_NOTE: Record<OpenTarget, string> = {
  same: "いまの画面を機器情報に切り替えます",
  tab: "元の画面はタブに残ります",
  window: "機器ごとに窓が開くので、並べて見比べられます",
};
