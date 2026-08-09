import { Link } from "react-router-dom";

import type { Device } from "../api/types";
import { DEVICE_TYPE_LABEL, formatDate, formatRemaining } from "../lib/format";
import SeverityBadge from "./SeverityBadge";

export default function DeviceTable({ devices }: { devices: Device[] }) {
  if (devices.length === 0) {
    return <div className="empty">該当する機器はありません</div>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>ホスト名</th>
          <th>種別</th>
          <th>ベンダー / 型番</th>
          <th>バージョン</th>
          <th>拠点</th>
          <th>保守期限</th>
          <th>EoL</th>
          <th>状態</th>
        </tr>
      </thead>
      <tbody>
        {devices.map((d) => (
          <tr key={d.id}>
            <td>
              <Link to={`/devices/${d.id}`}>{d.hostname}</Link>
            </td>
            <td>{DEVICE_TYPE_LABEL[d.device_type]}</td>
            <td>
              {d.vendor} / {d.model}
            </td>
            <td className="muted mono">{d.os_version ?? "—"}</td>
            <td className="muted">
              {d.site_id ? (
                <Link to={`/topology?site=${d.site_id}`} title="この拠点だけの構成図を開きます">
                  {d.site_name ?? "—"}
                </Link>
              ) : (
                (d.site_name ?? "—")
              )}
            </td>
            <td>
              {formatDate(d.lifecycle.maintenance_end)}
              <span className="muted">
                {" "}
                ({formatRemaining(d.lifecycle.days_to_maintenance_end)})
              </span>
            </td>
            <td>
              {formatDate(d.eol_date)}
              <span className="muted"> ({formatRemaining(d.lifecycle.days_to_eol)})</span>
            </td>
            <td>
              <SeverityBadge severity={d.lifecycle.overall_severity} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
