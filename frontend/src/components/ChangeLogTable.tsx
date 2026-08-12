import { Link } from "react-router-dom";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { ChangeAction, ChangeLog, ChangeSource } from "../api/types";
import { formatTimestamp } from "../lib/format";

export const ACTION_LABEL: Record<ChangeAction, string> = {
  create: "追加",
  update: "変更",
  delete: "削除",
};

export const ACTION_COLOR: Record<ChangeAction, string> = {
  create: "var(--sev-ok)",
  update: "var(--accent)",
  delete: "var(--sev-expired)",
};

export const SOURCE_LABEL: Record<ChangeSource, string> = {
  ui: "画面操作",
  import: "台帳の取り込み",
  seed: "デモデータ",
  system: "システム",
};

export const ENTITY_LABEL: Record<string, string> = {
  device: "機器",
  contract: "保守契約",
  site: "拠点",
};

/** 「A → B」の形で差分を見せる。追加・削除はまとめた内容を出す。 */
function Diff({ log }: { log: ChangeLog }) {
  if (log.action === "update") {
    return (
      <>
        <span className="muted">{log.old_value ?? "（空）"}</span>
        {" → "}
        <strong>{log.new_value ?? "（空）"}</strong>
      </>
    );
  }
  return <span className="muted">{log.new_value ?? log.old_value ?? "—"}</span>;
}

/** 戻せる履歴か。作成・削除は戻さない（消すのは削除であって、戻すとは違う） */
function canRevert(log: ChangeLog): boolean {
  return log.entity_type === "device" && log.action === "update" && Boolean(log.field);
}

export default function ChangeLogTable({
  logs,
  showEntity = true,
}: {
  logs: ChangeLog[];
  showEntity?: boolean;
}) {
  const queryClient = useQueryClient();
  const [failed, setFailed] = useState<{ id: number; reason: string } | null>(null);

  const undo = useMutation({
    mutationFn: (id: number) => api.revertChange(id),
    onSuccess: () => {
      setFailed(null);
      for (const key of ["changes", "device", "devices", "topology"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (error, id) => setFailed({ id, reason: (error as Error).message }),
  });

  if (logs.length === 0) {
    return <div className="empty">変更履歴はありません</div>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>日時</th>
          {showEntity && <th>対象</th>}
          <th>操作</th>
          <th>項目</th>
          <th>変更内容</th>
          <th>発生元</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {logs.map((log) => (
          <tr key={log.id}>
            <td className="muted">{formatTimestamp(log.created_at)}</td>
            {showEntity && (
              <td>
                <span className="muted">{ENTITY_LABEL[log.entity_type] ?? log.entity_type}</span>{" "}
                {log.entity_type === "device" && log.entity_id ? (
                  <Link to={`/devices/${log.entity_id}`}>{log.entity_label}</Link>
                ) : (
                  log.entity_label
                )}
              </td>
            )}
            <td style={{ color: ACTION_COLOR[log.action], fontWeight: 600 }}>
              {ACTION_LABEL[log.action]}
            </td>
            <td>{log.field_label ?? "—"}</td>
            <td style={{ whiteSpace: "normal", maxWidth: 520 }}>
              <Diff log={log} />
            </td>
            <td className="muted">{SOURCE_LABEL[log.source] ?? log.source}</td>
            <td className="nowrap">
              {canRevert(log) && (
                <button
                  onClick={() => undo.mutate(log.id)}
                  disabled={undo.isPending}
                  title="この変更を元の値に戻します。戻したことも履歴に残ります"
                >
                  戻す
                </button>
              )}
              {failed?.id === log.id && <div className="error">{failed.reason}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
