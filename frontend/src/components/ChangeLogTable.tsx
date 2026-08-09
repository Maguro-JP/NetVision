import { Link } from "react-router-dom";

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

export default function ChangeLogTable({
  logs,
  showEntity = true,
}: {
  logs: ChangeLog[];
  showEntity?: boolean;
}) {
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
          </tr>
        ))}
      </tbody>
    </table>
  );
}
