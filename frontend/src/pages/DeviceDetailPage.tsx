import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { MaintenanceContract } from "../api/types";
import ConfigDropZone from "../components/ConfigDropZone";
import ChangeLogTable from "../components/ChangeLogTable";
import QueryError from "../components/QueryError";
import { DOC_TYPE_LABEL, versionRange } from "../lib/docs";
import SeverityBadge from "../components/SeverityBadge";
import {
  DEVICE_STATUS_LABEL,
  DEVICE_TYPE_LABEL,
  formatCurrency,
  formatDate,
  formatRemaining,
} from "../lib/format";

const EMPTY_CONTRACT = {
  provider: "",
  contract_number: "",
  service_level: "",
  start_date: "",
  end_date: "",
  annual_cost: "",
  contact: "",
};

export default function DeviceDetailPage() {
  const { id } = useParams();
  const deviceId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [contract, setContract] = useState({ ...EMPTY_CONTRACT });
  const [showForm, setShowForm] = useState(false);
  const [showDesign, setShowDesign] = useState(false);

  const {
    data: device,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["device", id],
    queryFn: () => api.getDevice(deviceId),
  });

  const { data: deviceDocuments } = useQuery({
    queryKey: ["deviceDocuments", id],
    queryFn: () => api.documentsForDevice(deviceId),
  });

  // ポートの VLAN 設定。アクセス / ネイティブ / トランクを分けて見せる
  const { data: interfaces } = useQuery({
    queryKey: ["deviceInterfaces", id],
    queryFn: () => api.listInterfaces(deviceId),
  });

  // 図から起こす想定 Config。開いたときだけ取りに行く
  const designConfig = useQuery({
    queryKey: ["designConfig", id],
    queryFn: () => api.designConfig(deviceId),
    enabled: showDesign,
  });

  const { data: vulnerabilities } = useQuery({
    queryKey: ["deviceVulnerabilities", id],
    queryFn: () => api.vulnerabilityMatches(deviceId),
  });

  // この機器に対する変更と、紐づく保守契約への変更をまとめて見せる
  const { data: history } = useQuery({
    queryKey: ["deviceChanges", id],
    queryFn: () => api.listChanges({ q: device?.hostname, limit: 50 }),
    enabled: Boolean(device?.hostname),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["device", id] });
    queryClient.invalidateQueries({ queryKey: ["devices"] });
    queryClient.invalidateQueries({ queryKey: ["summary"] });
    queryClient.invalidateQueries({ queryKey: ["deviceChanges", id] });
    queryClient.invalidateQueries({ queryKey: ["changes"] });
  };

  const addContract = useMutation({
    mutationFn: () =>
      api.createContract(deviceId, {
        ...contract,
        contract_number: contract.contract_number || null,
        service_level: contract.service_level || null,
        contact: contract.contact || null,
        annual_cost: contract.annual_cost ? Number(contract.annual_cost) : null,
      } as Partial<MaintenanceContract>),
    onSuccess: () => {
      setContract({ ...EMPTY_CONTRACT });
      setShowForm(false);
      refresh();
    },
  });

  const removeContract = useMutation({
    mutationFn: (contractId: number) => api.deleteContract(contractId),
    onSuccess: refresh,
  });

  const removeDevice = useMutation({
    mutationFn: () => api.deleteDevice(deviceId),
    onSuccess: () => {
      refresh();
      navigate("/devices");
    },
  });

  if (isLoading) return <p className="muted">読み込み中…</p>;
  if (error) return <QueryError error={error} onRetry={() => refetch()} />;
  if (!device) return null;

  const lc = device.lifecycle;
  const set = (key: keyof typeof EMPTY_CONTRACT) => (e: { target: { value: string } }) =>
    setContract((prev) => ({ ...prev, [key]: e.target.value }));

  return (
    <>
      <h1>
        {device.hostname} <SeverityBadge severity={lc.overall_severity} />
      </h1>
      <p className="subtitle">
        <Link to="/devices">← 機器一覧</Link>
      </p>

      <div className="toolbar">
        <Link to={`/devices/${device.id}/edit`}>
          <button className="primary">編集</button>
        </Link>
        <button
          className="danger"
          onClick={() => {
            if (confirm(`${device.hostname} を削除します。よろしいですか？`)) {
              removeDevice.mutate();
            }
          }}
        >
          削除
        </button>
      </div>

      <h2>基本情報</h2>
      <div className="kv">
        <div>
          <span className="k">ベンダー / 型番</span>
          <span>
            {device.vendor} / {device.model}
          </span>
        </div>
        <div>
          <span className="k">種別 / 状態</span>
          <span>
            {DEVICE_TYPE_LABEL[device.device_type]} / {DEVICE_STATUS_LABEL[device.status]}
          </span>
        </div>
        <div>
          <span className="k">シリアル番号</span>
          <span>{device.serial_number ?? "—"}</span>
        </div>
        <div>
          <span className="k">OS / バージョン</span>
          <span>
            {device.os_name ?? "—"} {device.os_version ?? ""}
          </span>
        </div>
        <div>
          <span className="k">ファームウェア</span>
          <span>{device.firmware_version ?? "—"}</span>
        </div>
        <div>
          <span className="k">設置場所</span>
          <span>
            {device.site_id ? (
              <Link to={`/topology?site=${device.site_id}`} title="この拠点の構成図を開きます">
                {device.site_name ?? "—"}
              </Link>
            ) : (
              (device.site_name ?? "—")
            )}
            {device.rack ? ` / ${device.rack}` : ""}
            {device.rack_unit ? ` ${device.rack_unit}` : ""}
          </span>
        </div>
        <div>
          <span className="k">管理IP</span>
          <span>{device.mgmt_ip ?? "—"}</span>
        </div>
        <div>
          <span className="k">導入日 / 経過年数</span>
          <span>
            {formatDate(device.install_date)}
            {lc.age_years !== null ? ` (${lc.age_years}年)` : ""}
          </span>
        </div>
      </div>

      <h2>ライフサイクル</h2>
      <div className="kv">
        <div>
          <span className="k">保守期限</span>
          <span>
            {formatDate(lc.maintenance_end)}{" "}
            <span className="muted">{formatRemaining(lc.days_to_maintenance_end)}</span>{" "}
            <SeverityBadge severity={lc.maintenance_severity} />
          </span>
        </div>
        <div>
          <span className="k">販売終了 (EoS)</span>
          <span>
            {formatDate(lc.effective_eos_date)}{" "}
            <span className="muted">{formatRemaining(lc.days_to_eos)}</span>
          </span>
        </div>
        <div>
          <span className="k">
            サポート終了 (EoL)
            {lc.eol_source === "product" && (
              <span className="muted">（型番の製品マスタ由来）</span>
            )}
          </span>
          <span>
            {formatDate(lc.effective_eol_date)}{" "}
            <span className="muted">{formatRemaining(lc.days_to_eol)}</span>{" "}
            <SeverityBadge severity={lc.eol_severity} />
          </span>
        </div>
      </div>

      <h2>図から想定 Config を起こす</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        トポロジ図に描いた結線と VLAN 台帳から、この機器の Config を組み立てます。
        <strong>新規構築やテスト環境</strong>で、図を描いてから書き起こす手間を減らすためのものです。
        図に無い情報（アクセスポート・経路・ACL）は起こせないので、その箇所は印を残します。
      </p>
      <div className="toolbar">
        <button onClick={() => setShowDesign((value) => !value)}>
          {showDesign ? "閉じる" : "想定 Config を見る"}
        </button>
        {designConfig.data && (
          <span className="muted">
            結線 {designConfig.data.link_count} 本 / VLAN {designConfig.data.vlan_count} 件から生成
          </span>
        )}
      </div>
      {showDesign && (
        <>
          {designConfig.isLoading && <p className="muted">組み立て中…</p>}
          {designConfig.error && (
            <div className="error">{(designConfig.error as Error).message}</div>
          )}
          {designConfig.data && (
            <>
              {designConfig.data.link_count === 0 && (
                <div className="empty" style={{ marginBottom: 10 }}>
                  この機器にはまだ線が引かれていません。
                  <Link to={`/topology`}> トポロジ図</Link>
                  で結線してから開くと、ポートの設定まで入ります。
                </div>
              )}
              <pre className="config-preview">{designConfig.data.body}</pre>
              <p className="muted">
                そのまま投入せず、内容を確認してください。計画として残す場合は
                <Link to="/config-plans"> Config計画</Link>
                で「図から起こす」を選ぶと、差分の生成と収束検証まで通せます。
              </p>
            </>
          )}
        </>
      )}

      <h2>ポート</h2>
      <ConfigDropZone deviceId={deviceId} hostname={device.hostname} />
      {!interfaces || interfaces.length === 0 ? (
        <div className="empty">
          Config を取り込むと、ポートと VLAN の設定がここに出ます。
        </div>
      ) : (
        <>
          <p className="muted" style={{ marginTop: -6 }}>
            実機の Config から読み取った内容です。アクセス・ネイティブ・トランクは
            別々に持っています（混ぜると片側だけの食い違いが見えなくなるため）。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>状態</th>
                  <th>ポート</th>
                  <th>説明</th>
                  <th>モード</th>
                  <th>アクセス VLAN</th>
                  <th>ネイティブ VLAN</th>
                  <th>トランク許可 VLAN</th>
                  <th>VRF</th>
                  <th>IP</th>
                </tr>
              </thead>
              <tbody>
                {interfaces.map((port) => (
                  <tr key={port.id}>
                    <td className="nowrap">
                      {port.enabled ? (
                        <span className="badge ok" title="Config で shutdown されていません">
                          有効
                        </span>
                      ) : (
                        <span className="badge unknown" title="Config で shutdown されています">
                          shutdown
                        </span>
                      )}
                    </td>
                    <td>
                      <strong>{port.name}</strong>
                    </td>
                    <td className="muted">{port.description ?? "—"}</td>
                    <td>{port.mode ?? "—"}</td>
                    <td>{port.vlan ?? "—"}</td>
                    <td>{port.native_vlan ?? "—"}</td>
                    <td
                      title={
                        port.trunk_vlans === "all"
                          ? "許可 VLAN を書いていないトランクです。定義されている VLAN が全部通ります"
                          : undefined
                      }
                    >
                      {port.trunk_vlans ?? "—"}
                    </td>
                    <td>{port.vrf ?? "—"}</td>
                    <td className="muted">{port.ip_address ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>保守契約</h2>
      {device.contracts.length === 0 ? (
        <div className="empty">保守契約が登録されていません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>契約番号</th>
              <th>保守ベンダー</th>
              <th>サービスレベル</th>
              <th>期間</th>
              <th>年額</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {device.contracts.map((c) => (
              <tr key={c.id}>
                <td>{c.contract_number ?? "—"}</td>
                <td>{c.provider}</td>
                <td>{c.service_level ?? "—"}</td>
                <td>
                  {formatDate(c.start_date)} 〜 {formatDate(c.end_date)}
                </td>
                <td>{formatCurrency(c.annual_cost)}</td>
                <td>
                  <button className="danger" onClick={() => removeContract.mutate(c.id)}>
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="actions">
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "入力をやめる" : "保守契約を追加"}
        </button>
      </div>

      {showForm && (
        <form
          style={{ marginTop: 12 }}
          onSubmit={(e) => {
            e.preventDefault();
            addContract.mutate();
          }}
        >
          {addContract.error && <div className="error">{(addContract.error as Error).message}</div>}
          <div className="form-grid">
            <div className="field">
              <label htmlFor="provider">保守ベンダー *</label>
              <input id="provider" required value={contract.provider} onChange={set("provider")} />
            </div>
            <div className="field">
              <label htmlFor="contract_number">契約番号</label>
              <input
                id="contract_number"
                value={contract.contract_number}
                onChange={set("contract_number")}
              />
            </div>
            <div className="field">
              <label htmlFor="service_level">サービスレベル</label>
              <input
                id="service_level"
                placeholder="24x365 4時間駆付 など"
                value={contract.service_level}
                onChange={set("service_level")}
              />
            </div>
            <div className="field">
              <label htmlFor="start_date">開始日 *</label>
              <input
                id="start_date"
                type="date"
                required
                value={contract.start_date}
                onChange={set("start_date")}
              />
            </div>
            <div className="field">
              <label htmlFor="end_date">終了日 *</label>
              <input
                id="end_date"
                type="date"
                required
                value={contract.end_date}
                onChange={set("end_date")}
              />
            </div>
            <div className="field">
              <label htmlFor="annual_cost">年額（円）</label>
              <input
                id="annual_cost"
                type="number"
                min={0}
                value={contract.annual_cost}
                onChange={set("annual_cost")}
              />
            </div>
            <div className="field">
              <label htmlFor="contact">連絡先</label>
              <input id="contact" value={contract.contact} onChange={set("contact")} />
            </div>
          </div>
          <div className="actions">
            <button className="primary" type="submit" disabled={addContract.isPending}>
              追加
            </button>
          </div>
        </form>
      )}

      <h2>この機器の資料</h2>
      {!deviceDocuments || deviceDocuments.length === 0 ? (
        <div className="empty">
          該当する資料がありません。「ベンダー資料」から登録できます。
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>資料</th>
              <th>種別</th>
              <th>適用バージョン</th>
              <th>該当理由</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {deviceDocuments.map((doc) => (
              <tr key={doc.id}>
                <td>
                  {doc.title}
                  {doc.superseded_by_title && (
                    <div className="muted">新版あり: {doc.superseded_by_title}</div>
                  )}
                </td>
                <td className="muted">{DOC_TYPE_LABEL[doc.doc_type]}</td>
                <td>{versionRange(doc.applies_from, doc.applies_to, doc.version_label)}</td>
                <td className="muted" style={{ whiteSpace: "normal" }}>
                  {doc.reason}
                </td>
                <td>
                  {doc.has_file ? (
                    <a href={api.documentDownloadUrl(doc.id)}>開く</a>
                  ) : doc.source_url ? (
                    <a href={doc.source_url} target="_blank" rel="noreferrer">
                      入手元
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>該当する脆弱性</h2>
      {!vulnerabilities || vulnerabilities.findings.length === 0 ? (
        <div className="empty">
          {device.os_version
            ? "該当する脆弱性はありません"
            : "OSバージョンが未登録のため判定できません"}
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>CVE</th>
              <th>CVSS</th>
              <th>状況</th>
              <th>判定根拠</th>
              <th>修正版</th>
            </tr>
          </thead>
          <tbody>
            {vulnerabilities.findings.map((finding) => (
              <tr key={finding.cve_id}>
                <td>{finding.cve_id}</td>
                <td>{finding.cvss_score ?? "—"}</td>
                <td>
                  {finding.known_exploited && (
                    <span className="badge expired">悪用確認済み</span>
                  )}
                </td>
                <td className="muted" style={{ whiteSpace: "normal" }}>
                  {finding.matched_on}
                </td>
                <td>{finding.fixed_version ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>変更履歴</h2>
      <ChangeLogTable logs={history?.items ?? []} showEntity={false} />

      {device.notes && (
        <>
          <h2>備考</h2>
          <div className="kv">
            <div style={{ whiteSpace: "pre-wrap" }}>{device.notes}</div>
          </div>
        </>
      )}
    </>
  );
}
