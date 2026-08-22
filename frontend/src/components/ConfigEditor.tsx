import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import type { ConfigEdit } from "../api/types";

/**
 * Config を見ながら、選んだところを直してもらう。
 *
 * 全部を作り直すと、どこが変わったのかを人が突き合わせることになり、結局
 * 読み切れない。選んだ範囲だけを渡して、その範囲だけを直してもらう。
 *
 * 返ってきたものは差分で見せる。上書きした結果だけを見せると、確認が飛ばされる。
 *
 * Config を出す画面はいくつもある（将来フル、更改、想定 Config、起こした構成）。
 * どこでも同じ直し方ができるように、ここだけに置いて呼び出す。
 *
 * `onApply` を渡すと、採った結果を呼び出し側へ返す。渡さない場合は、この中の
 * 表示だけが変わる（元の計画や台帳には触らない）。
 */

/** どう直したかの呼び名。規則で直したのかモデルが直したのかで、確かめ方が変わる。 */
export const EDIT_SOURCE_LABEL: Record<string, string> = {
  rule: "決まった直し方で",
  model: "手元のモデルが",
  prompt: "直せませんでした",
};

/** 選んだ範囲を、行の切れ目にそろえる。行の途中で切ると貼れない設定になる。 */
export function snapToLines(text: string, start: number, end: number): string {
  if (start === end) return "";
  const head = text.lastIndexOf("\n", start - 1) + 1;
  const tailBreak = text.indexOf("\n", end);
  const tail = tailBreak === -1 ? text.length : tailBreak;
  return text.slice(head, tail);
}

export default function ConfigEditor({
  body,
  title = "選んだところを直してもらう",
  onApply,
}: {
  body: string;
  title?: string;
  /** 採った結果の受け取り先。渡さなければ、この中の表示だけが変わる */
  onApply?: (next: string) => void;
}) {
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(body);
  const [source, setSource] = useState(body);
  const [selection, setSelection] = useState("");
  const [request, setRequest] = useState("");
  const [result, setResult] = useState<ConfigEdit | null>(null);

  // 呼び出し側が別の Config を出したら、こちらも入れ替える
  //（前の機器の設定を編集し続ける、が起きないように）
  if (body !== source) {
    setSource(body);
    setText(body);
    setSelection("");
    setResult(null);
  }

  const run = useMutation({
    mutationFn: () => api.editSelection(selection, request),
    onSuccess: setResult,
  });

  const pick = () => {
    const area = areaRef.current;
    if (!area) return;
    setSelection(snapToLines(text, area.selectionStart, area.selectionEnd));
    setResult(null);
  };

  const take = () => {
    if (!result || !result.changed) return;
    const next = text.replace(result.before, result.after);
    setText(next);
    setSelection("");
    setResult(null);
    onApply?.(next);
  };

  return (
    <>
      <h2>{title}</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        直したいところを選んで、どう直したいかを書いてください。選んだ範囲だけを直します。
        結果は差分で出るので、採るかどうかはそれを見て決めてください。
      </p>

      <textarea
        ref={areaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onMouseUp={pick}
        onKeyUp={pick}
        rows={16}
        spellCheck={false}
        style={{ width: "100%", fontFamily: "monospace" }}
      />

      {selection ? (
        <>
          <p className="muted">
            選んでいる範囲（{selection.split("\n").length} 行）
          </p>
          <pre className="config-preview">{selection}</pre>
        </>
      ) : (
        <p className="muted">
          上の Config から、直したいところを選んでください。
        </p>
      )}

      <div className="toolbar">
        <input
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="例: このポートは使わないので閉じて / 説明を「to floor3-sw-01」にして"
          style={{ minWidth: 420 }}
        />
        <button
          className="primary"
          disabled={!selection || !request.trim() || run.isPending}
          onClick={() => run.mutate()}
        >
          {run.isPending ? "直しています…" : "直してもらう"}
        </button>
      </div>
      {run.error && <div className="error">{(run.error as Error).message}</div>}

      {result && (
        <div className="card">
          <div className="muted">
            {EDIT_SOURCE_LABEL[result.source] ?? result.source}
          </div>
          {result.changed ? (
            <>
              <pre className="diff">
                {result.diff.split("\n").map((line, index) => (
                  <div
                    key={index}
                    className={
                      line.startsWith("+")
                        ? "diff-add"
                        : line.startsWith("-")
                          ? "diff-del"
                          : line.startsWith("@@")
                            ? "diff-hunk"
                            : ""
                    }
                  >
                    {line}
                  </div>
                ))}
              </pre>
              <div className="actions">
                <button className="primary" onClick={take}>
                  この直しを採る
                </button>
                <button onClick={() => setResult(null)}>採らない</button>
              </div>
            </>
          ) : (
            <p className="muted">変わりませんでした。</p>
          )}
          <ul className="muted">
            {result.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
          {result.prompt && (
            <>
              <p className="muted">
                この依頼文を貼って、返ってきた行を貼り戻してください。
              </p>
              <pre className="config-preview">{result.prompt}</pre>
            </>
          )}
        </div>
      )}
    </>
  );
}
