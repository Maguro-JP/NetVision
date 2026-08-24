import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import QueryError from "../components/QueryError";
import { formatTimestamp } from "../lib/format";

function formatBytes(value: number): string {
  if (value < 1024) return `${value}バイト`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 表の形を更新する専用ページ。
 *
 * 差分があるときは起動時にここへ案内する。控えが取れてから表を更新し、
 * 問題ないことを確認するまで控えは消さない。
 */
export default function MigrationPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["schema"],
    queryFn: api.schemaStatus,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["schema"] });
    queryClient.invalidateQueries({ queryKey: ["meta"] });
  };

  const apply = useMutation({
    mutationFn: api.applySchema,
    onSuccess: (status) => {
      queryClient.setQueryData(["schema"], status);
      invalidate();
    },
  });

  const confirm = useMutation({
    mutationFn: api.confirmSchema,
    onSuccess: (status) => {
      queryClient.setQueryData(["schema"], status);
      invalidate();
    },
  });

  if (isLoading) {
    return (
      <div className="migration-page">
        <p>表の状態を確認しています…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="migration-page">
        <h1>表の更新</h1>
        <QueryError error={error} onRetry={() => refetch()} />
      </div>
    );
  }

  const backup = data.unconfirmed[0];
  const actionError = apply.error ?? confirm.error;

  return (
    <div className="migration-page">
      <p className="migration-brand">
        Net<span>Vision</span>
      </p>
      <h1>表の更新</h1>

      {data.phase === "needs_apply" && (
        <>
          <p>
            アプリの新しい版は、データの入れ物の形が変わっています。中身（機器や結線、Config
            の原文）は残します。先に控えを取ってから更新します。控えは、問題ないことを確認するまで消しません。
          </p>
          {data.counts && (
            <p>
              いま入っているデータは、機器 {data.counts.devices.toLocaleString()} 台、拠点{" "}
              {data.counts.sites.toLocaleString()} か所です。
            </p>
          )}
          {data.pending.length > 0 && (
            <div className="migration-steps">
              <h2>当たる内容</h2>
              <ol>
                {data.pending.map((step) => (
                  <li key={step.revision}>
                    <code>{step.revision}</code>
                    <span>{step.summary}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <p className="muted">
            いまの版: {data.current_revision ?? "（記録なし）"} ／ 新しい版: {data.head_revision}
          </p>
          <button
            type="button"
            className="primary"
            disabled={apply.isPending}
            onClick={() => apply.mutate()}
          >
            {apply.isPending ? "控えを取って更新しています…" : "控えを取って更新する"}
          </button>
        </>
      )}

      {data.phase === "needs_confirm" && backup && (
        <>
          <p>
            表は更新済みです。機器の台数や構成図を見て、問題ないことを確かめてください。
            控えは確認するまで残します。確認したあともファイルは消しません。
          </p>
          <BackupCard backup={backup} />
          {data.counts && (
            <p>
              更新後のデータは、機器 {data.counts.devices.toLocaleString()} 台、拠点{" "}
              {data.counts.sites.toLocaleString()} か所です。更新前と台数が違うときは、控えから戻してください。
            </p>
          )}
          <ol className="migration-check">
            <li>
              <Link to="/devices">機器一覧</Link>で台数を見る
            </li>
            <li>
              <Link to="/topology">構成図</Link>で拠点を選んで図が出るか見る
            </li>
            <li>
              <Link to="/configs">Configの取り込み</Link>で履歴が残っているか見る
            </li>
          </ol>
          <button
            type="button"
            className="primary"
            disabled={confirm.isPending}
            onClick={() => confirm.mutate()}
          >
            {confirm.isPending ? "記録しています…" : "問題ない。通常の画面へ"}
          </button>
        </>
      )}

      {data.phase === "ready" && (
        <>
          <p>表は最新です。通常の画面を使えます。</p>
          <p>
            <Link to="/dashboard">ダッシュボードへ</Link>
          </p>
        </>
      )}

      {actionError && (
        <div className="error" role="alert">
          {actionError instanceof Error ? actionError.message : "処理に失敗しました"}
        </div>
      )}
    </div>
  );
}

function BackupCard({
  backup,
}: {
  backup: {
    path: string;
    created_at: string;
    bytes: number;
    kind: string;
  };
}) {
  return (
    <div className="migration-backup">
      <h2>控えの場所</h2>
      <p>
        <code className="migration-path">{backup.path}</code>
      </p>
      <p>
        {formatTimestamp(backup.created_at)} ／ {formatBytes(backup.bytes)}
        {backup.kind === "sqlite" ? " ／ SQLite ファイルのコピー" : " ／ Postgres のダンプ"}
      </p>
      <p className="muted">
        戻すときはアプリを止めて、このフォルダの中身をデータの置き場所へ戻します。SQL
        を手で書く必要はありません。
      </p>
    </div>
  );
}
