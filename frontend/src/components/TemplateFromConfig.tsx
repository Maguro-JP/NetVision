import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { ConfigTemplateDraft } from "../api/types";

/**
 * いま動いている Config から、テンプレートを起こす。
 *
 * 同じ役割の機器の Config は、たいてい8割が同じ。その共通部分がその現場の
 * テンプレートになる。人が最初から書くより早く、少なくとも動く形にはなって
 * いる（実機に入っているので）。
 */

/** 何から起こしたのかを1行で。あとで「これ何から作ったの」に答えるため。 */
export function sourceLine(draft: ConfigTemplateDraft): string {
  const names = draft.hostnames.slice(0, 4).join(", ");
  const rest = draft.hostnames.length > 4 ? " ほか" : "";
  return `${draft.devices_seen} 台（${names}${rest}）の Config から`;
}

export default function TemplateFromConfig() {
  const queryClient = useQueryClient();
  const [prefix, setPrefix] = useState("");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<ConfigTemplateDraft | null>(null);

  const make = useMutation({
    mutationFn: () =>
      api.templateFromConfig({ hostname_prefix: prefix || undefined }),
    onSuccess: (data) => {
      setDraft(data);
      if (!name) setName(data.name);
    },
  });

  const save = useMutation({
    mutationFn: () =>
      api.saveTemplateFromConfig({
        hostname_prefix: prefix || undefined,
        name: name || undefined,
      }),
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["configTemplates"] });
    },
  });

  return (
    <>
      <h2>いま動いている Config から起こす</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        同じ役割の機器の Config
        を重ねて、共通のところを取り出します。機器ごとに違う値は
        入れる欄になります。全部同じ値だったところは、そのまま残します。
      </p>
      <div className="form-grid">
        <label>
          対象を絞る（ホスト名の頭）
          <input
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="hq-sw-"
          />
        </label>
        <label>
          テンプレートの名前
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="拠点スイッチ標準"
          />
        </label>
      </div>
      <div className="actions">
        <button disabled={make.isPending} onClick={() => make.mutate()}>
          {make.isPending ? "重ねています…" : "下書きを作る"}
        </button>
        <button
          className="primary"
          disabled={!draft || save.isPending}
          onClick={() => save.mutate()}
        >
          テンプレートとして登録
        </button>
      </div>
      {make.error && (
        <div className="error">{(make.error as Error).message}</div>
      )}
      {save.error && (
        <div className="error">{(save.error as Error).message}</div>
      )}

      {draft && (
        <>
          <p className="muted">{sourceLine(draft)}</p>
          <pre className="config-preview">{draft.body}</pre>
          {draft.variables.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>入れる欄</th>
                  <th>初期値</th>
                  <th>なぜ欄にしたか</th>
                </tr>
              </thead>
              <tbody>
                {draft.variables.map((variable) => (
                  <tr key={variable.name}>
                    <td>{variable.name}</td>
                    <td>{variable.default}</td>
                    <td>{variable.help}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ul className="muted">
            {draft.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
