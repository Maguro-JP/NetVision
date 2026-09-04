import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import WindowLink, { deviceWindow } from "./WindowLink";
import type { Device } from "../api/types";
import {
  DEVICE_STATUS_LABEL,
  DEVICE_TYPE_LABEL,
  formatDate,
  formatRemaining,
} from "../lib/format";
import SeverityBadge from "./SeverityBadge";

/** 列。並び順は見る頻度の順で、状態を左に置く。
 *
 * 一覧を開いて最初に見るのは「困っている機器はどれか」で、機器の名前はその次。
 * 状態が右端にあると、横に長い表を目で往復することになる。
 *
 * 状態の欄には、保守の状態・運用の状態・新着をまとめて置く。どれも
 * 「いまこの機器はどういう扱いか」の話で、別の列に散らすと目が行き来する。
 */
const COLUMNS = [
  "status",
  "operation",
  "hostname",
  "type",
  "product",
  "site",
  "version",
  "maintenance",
  "eol",
] as const;

export type DeviceColumn = (typeof COLUMNS)[number];

const HEADINGS: Record<DeviceColumn, string> = {
  status: "保守",
  hostname: "管理ID / ホスト名",
  type: "種別",
  operation: "運用",
  product: "メーカー / 型番",
  site: "拠点",
  version: "バージョン",
  maintenance: "保守期限",
  eol: "EoL",
};

/** 新着と見なす日数。取り込んだ直後に見つけられれば足りる。 */
const NEW_DAYS = 7;

function isNew(device: Device): boolean {
  if (!device.created_at) return false;
  const added = new Date(device.created_at).getTime();
  if (Number.isNaN(added)) return false;
  return Date.now() - added < NEW_DAYS * 24 * 60 * 60 * 1000;
}

/**
 * 新しく載った機器を上に出す並び。
 *
 * Config を取り込んだ直後は、増えた機器を確かめたい。名前順のままだと
 * 何十行の途中に紛れて見つけられない。新着だけ先頭へ寄せ、その中と
 * それ以外はホスト名順にする（並びが日によって変わらないように）。
 */
export function sortForList(devices: Device[]): Device[] {
  return [...devices].sort((a, b) => {
    const fresh = Number(isNew(b)) - Number(isNew(a));
    if (fresh) return fresh;
    return a.hostname.localeCompare(b.hostname);
  });
}

/** 未入力の項目。空欄のままだと入れ忘れなのか元々無いのか分からない。 */
function Blank({ label }: { label: string }) {
  return (
    <span className="blank" title={`${label}が入っていません`}>
      未入力
    </span>
  );
}

export default function DeviceTable({
  devices,
  columnFilters,
}: {
  devices: Device[];
  /** 列見出しの下に出す絞り込み。Excel のように列ごとに絞れる */
  columnFilters?: Partial<Record<DeviceColumn, ReactNode>>;
}) {
  const rows = sortForList(devices);

  return (
    <table className="device-table">
      <thead>
        <tr>
          {COLUMNS.map((column) => (
            <th key={column}>{HEADINGS[column]}</th>
          ))}
        </tr>
        {columnFilters && (
          <tr className="filter-row">
            {COLUMNS.map((column) => (
              <th key={column}>{columnFilters[column] ?? null}</th>
            ))}
          </tr>
        )}
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr>
            <td colSpan={COLUMNS.length} className="empty-cell">
              該当する機器はありません
            </td>
          </tr>
        )}
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="nowrap">
              <SeverityBadge severity={d.lifecycle.overall_severity} />
              {d.missing_fields.length > 0 && (
                <span
                  className="badge warning"
                  title={`${d.missing_fields.join("・")}が入っていません`}
                >
                  情報不足
                </span>
              )}
            </td>
            <td className="muted nowrap">
              {DEVICE_STATUS_LABEL[d.status]}
              {/* 新着は運用の隣に置く。名前の下に足すと、機器の名前が3行になる */}
              {isNew(d) && <span className="badge new">New</span>}
            </td>
            <td className="nowrap">
              {/* 管理IDが人の決めた番号で、ホスト名は機械の名前。
                  台帳を追うときは管理IDから探すので、上に置く */}
              {d.code ? (
                <div className="mono muted" title="管理ID">
                  {d.code}
                </div>
              ) : null}
              <WindowLink
                to={`/devices/${d.id}`}
                name={deviceWindow(d.id)}
                width={1100}
                height={900}
                title="機器情報を開きます（開き方は設定で変えられます）"
              >
                {d.hostname}
              </WindowLink>
            </td>
            <td>{DEVICE_TYPE_LABEL[d.device_type]}</td>
            <td>
              {d.vendor || <Blank label="メーカー" />} /{" "}
              {d.model || <Blank label="型番" />}
            </td>
            <td className="muted">
              {d.site_id ? (
                <Link
                  to={`/topology?site=${d.site_id}`}
                  title="この拠点だけの構成図を開きます"
                >
                  {d.site_name ?? "—"}
                </Link>
              ) : (
                <Blank label="拠点" />
              )}
            </td>
            <td className="muted mono">{d.os_version ?? "—"}</td>
            <td className="nowrap">
              {formatDate(d.lifecycle.maintenance_end)}
              <span className="muted">
                {" "}
                ({formatRemaining(d.lifecycle.days_to_maintenance_end)})
              </span>
            </td>
            <td className="nowrap">
              {/* 機器に日付が無ければ型番から引いた日付。残り日数と食い違わせない */}
              {formatDate(d.lifecycle.effective_eol_date)}
              <span className="muted">
                {" "}
                ({formatRemaining(d.lifecycle.days_to_eol)})
              </span>
              {d.lifecycle.eol_source === "product" && (
                <span
                  className="muted"
                  title="機器に日付が入っていないので、型番の情報を使っています"
                >
                  {" "}
                  型番から
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
