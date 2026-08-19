import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
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
  const { data: llm } = useQuery({
    queryKey: ["llmStatus"],
    queryFn: () => api.llmStatus(),
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

      <div className="card settings-card">
        <h2>資料を読ませるモデル</h2>
        <p className="muted">
          手元か社内に立てたモデルがあると、取り込んだベンダー資料を根拠に
          Config の
          下書きを作れます。置いていなくても、資料から拾ったコマンドまでは出ます。
          外の事業者へは、明示して許すまで一切送りません。設定の仕方は
          docs/LOCAL_LLM.md にあります。
        </p>
        {llm && !llm.enabled && (
          <p className="muted">
            いまは使わない設定です（NETVISION_LLM_ENABLED=false）。規則ベースだけで動きます。
          </p>
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
                      <span className="badge">設定が足りません</span>
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
    </div>
  );
}

const OPEN_TARGET_NOTE: Record<OpenTarget, string> = {
  same: "いまの画面を機器情報に切り替えます",
  tab: "元の画面はタブに残ります",
  window: "機器ごとに窓が開くので、並べて見比べられます",
};
