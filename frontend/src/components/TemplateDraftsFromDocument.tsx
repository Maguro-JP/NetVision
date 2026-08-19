import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";

/**
 * 資料からテンプレートを起こす。
 *
 * コマンドの書式は機種とバージョンで変わる。人が手で書き写すと、写し間違いと
 * 版ずれが必ず入る。資料の中に正しい書き方があるので、そこから起こす。
 *
 * 値の見分けは規則ベースなので外すこともある。登録したあと、変数の切り方を
 * 人が直す前提で出す。
 */
export default function TemplateDraftsFromDocument({
  documentId,
  title,
  onClose,
}: {
  documentId: number;
  title: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: drafts, isLoading } = useQuery({
    queryKey: ["templateDrafts", documentId],
    queryFn: () => api.templateDrafts(documentId),
  });

  const create = useMutation({
    mutationFn: (index: number) =>
      api.createTemplateFromDocument(documentId, index),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["configTemplates"] }),
  });

  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="toolbar">
        <strong>{title} からテンプレートを起こす</strong>
        <button onClick={onClose}>閉じる</button>
      </div>
      <p className="muted">
        資料に書かれているコマンドから下書きを作ります。番号・アドレス・ポート名は変数にし、
        資料に書いてあった値を初期値にしてあります。値の見分けは規則ベースなので、
        登録したあとに変数の切り方を直してください。
      </p>

      {isLoading && <p className="muted">読んでいます…</p>}
      {drafts?.length === 0 && (
        <div className="empty">
          この資料からはコマンドを拾えませんでした（読めない形式か、コマンドが書かれていない）
        </div>
      )}

      {(drafts ?? []).map((draft, index) => (
        <div key={index} className="card" style={{ marginTop: 8 }}>
          <strong>{draft.name}</strong>
          <div className="muted">
            {draft.vendor}
            {draft.model ? ` / ${draft.model}` : " / シリーズ全体"}
          </div>
          <pre className="diff" style={{ maxHeight: 180, overflow: "auto" }}>
            {draft.body}
          </pre>
          {draft.variables.length > 0 && (
            <p className="muted">
              変数:{" "}
              {draft.variables
                .map((item) => `${item.name}（${item.default}）`)
                .join(" / ")}
            </p>
          )}
          <div className="actions">
            <button
              className="primary"
              onClick={() => create.mutate(index)}
              disabled={create.isPending}
            >
              テンプレートとして登録
            </button>
          </div>
        </div>
      ))}

      {create.error && (
        <div className="error">{(create.error as Error).message}</div>
      )}
      {create.isSuccess && (
        <p className="ok">
          登録しました（Configテンプレートの画面で直せます）。
        </p>
      )}
    </div>
  );
}
