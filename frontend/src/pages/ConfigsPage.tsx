import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { IngestResult } from "../api/types";
import QueryError from "../components/QueryError";
import { formatTimestamp } from "../lib/format";

/** 取り込み1ファイル分の結果。読み取れたものと、台帳との差分を見せる。 */
function ResultCard({ result }: { result: IngestResult }) {
  const tone = result.matched ? "var(--sev-ok)" : "var(--sev-warning)";

  return (
    <div className="card" style={{ borderColor: tone, marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong>{result.filename}</strong>
        <span className="muted">{result.vendor_label}</span>
        {result.matched ? (
          <Link to={`/devices/${result.device_id}`}>{result.device_hostname}</Link>
        ) : (
          <span className="badge warning">台帳に未登録</span>
        )}
        {result.unchanged && <span className="badge unknown">前回と同じ内容</span>}
      </div>

      <div className="muted" style={{ marginTop: 6 }}>
        ホスト名 {result.hostname ?? "—"} ／ インターフェース {result.interface_count} ／ VLAN{" "}
        {result.vlan_count} ／ 隣接 {result.neighbor_count}
        {result.links_created > 0 && ` ／ 結線 ${result.links_created} 本を作成`}
      </div>

      {result.message && <div style={{ marginTop: 6 }}>{result.message}</div>}

      {result.applied_fields.length > 0 && (
        <div style={{ marginTop: 6, color: "var(--sev-ok)" }}>
          台帳へ反映: {result.applied_fields.join(" / ")}
        </div>
      )}

      {result.diffs.some((d) => d.differs) && (
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>項目</th>
              <th>台帳の値</th>
              <th>Config の値</th>
            </tr>
          </thead>
          <tbody>
            {result.diffs
              .filter((d) => d.differs)
              .map((d) => (
                <tr key={d.field}>
                  <td>{d.label}</td>
                  <td className="muted">{d.current ?? "（未登録）"}</td>
                  <td style={{ color: "var(--accent)" }}>{d.observed}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}

      {result.unresolved_neighbors.length > 0 && (
        <>
          <div style={{ marginTop: 10, color: "var(--sev-warning)" }}>
            台帳に無い機器と繋がっています（記載漏れの可能性）
          </div>
          <table>
            <thead>
              <tr>
                <th>自機のポート</th>
                <th>相手のホスト名</th>
                <th>相手の機種</th>
              </tr>
            </thead>
            <tbody>
              {result.unresolved_neighbors.map((n) => (
                <tr key={`${n.local_interface}-${n.remote_hostname}`}>
                  <td>{n.local_interface}</td>
                  <td>{n.remote_hostname}</td>
                  <td className="muted">{n.remote_platform ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default function ConfigsPage() {
  const queryClient = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [applyInventory, setApplyInventory] = useState(false);
  // 取り込んだのに機器一覧に何も出てこないのでは道具として使えないので、
  // 台帳に無いホスト名は既定で登録する。外せるようにはしておく
  const [createMissing, setCreateMissing] = useState(true);
  const [results, setResults] = useState<IngestResult[] | null>(null);
  const [diffTarget, setDiffTarget] = useState<number | null>(null);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const takeFiles = (list: FileList | File[] | null) => {
    const next = Array.from(list ?? []);
    if (!next.length) return;
    setFiles(next);
    setResults(null);
  };

  const { data: snapshots } = useQuery({ queryKey: ["configs"], queryFn: () => api.listConfigs() });
  const { data: diff } = useQuery({
    queryKey: ["configDiff", diffTarget],
    queryFn: () => api.diffConfig(diffTarget!),
    enabled: diffTarget !== null,
  });

  const run = useMutation({
    mutationFn: () => api.uploadConfigs(files, { applyInventory, createMissing }),
    onSuccess: (data) => {
      setResults(data);
      queryClient.invalidateQueries({ queryKey: ["configs"] });
      queryClient.invalidateQueries({ queryKey: ["topology"] });
      queryClient.invalidateQueries({ queryKey: ["devices"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteConfig(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configs"] }),
  });

  return (
    <>
      <h1>Config の取り込み</h1>
      <p className="subtitle">
        running-config や show 出力を読み込んで、機器情報・インターフェース・VLAN・隣接情報を抽出します。
        隣接情報（LLDP / CDP）からは <Link to="/topology">トポロジ図</Link> の結線を自動で組み立てます。
      </p>

      <div className="form-grid">
        <div className="field wide config-drop">
          <label>ファイル（複数可。Cisco / Juniper / Fortinet / NEC IX・QX）</label>
          <div
            className={`dropzone${over ? " over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              takeFiles(e.dataTransfer.files);
            }}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
            }}
          >
            <input
              ref={inputRef}
              id="files"
              type="file"
              multiple
              hidden
              onChange={(e) => {
                takeFiles(e.target.files);
                e.target.value = "";
              }}
            />
            {files.length === 0 ? (
              <span>
                ここに<strong>ドラッグ＆ドロップ</strong>
                <br />
                <span className="muted">クリックして選ぶこともできます</span>
              </span>
            ) : (
              <span>
                {files.length} 件を選びました
                <br />
                <span className="muted">{files.map((f) => f.name).join("、")}</span>
              </span>
            )}
          </div>
        </div>
        <div className="field">
          <label htmlFor="apply">読み取った型番・バージョンの扱い</label>
          <select
            id="apply"
            value={applyInventory ? "apply" : "diff"}
            onChange={(e) => setApplyInventory(e.target.value === "apply")}
          >
            <option value="diff">差分を表示するだけ（台帳は変更しない）</option>
            <option value="apply">台帳へ反映する</option>
          </select>
        </div>
        <div className="field wide">
          <label>
            <input
              type="checkbox"
              checked={createMissing}
              onChange={(e) => setCreateMissing(e.target.checked)}
            />{" "}
            台帳に無い機器を Config から登録する
          </label>
          <span className="muted">
            紐付けはファイル名ではなく、Config の中のホスト名で判断します。
            型番が読み取れなくても登録し、機器一覧に「情報不足」と表示します。
            型番は EoL と脆弱性を引く鍵なので、<code>show version</code> の出力も
            同じファイルに含めるか、あとから機器一覧で入力してください。
          </span>
        </div>
      </div>

      <div className="actions">
        <button
          className="primary"
          disabled={files.length === 0 || run.isPending}
          onClick={() => run.mutate()}
        >
          {run.isPending ? "取り込み中…" : `取り込む（${files.length}件）`}
        </button>
      </div>

      {run.error && <QueryError error={run.error} onRetry={() => run.mutate()} />}

      {results && (
        <>
          <h2>取り込み結果</h2>
          {results.map((result) => (
            <ResultCard key={`${result.filename}-${result.snapshot_id}`} result={result} />
          ))}
          <div className="actions">
            <Link to="/topology">
              <button className="primary">トポロジ図で確認する →</button>
            </Link>
          </div>
        </>
      )}

      <h2>取り込み履歴</h2>
      {(snapshots ?? []).length === 0 ? (
        <div className="empty">まだ Config を取り込んでいません</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>取り込み日時</th>
              <th>ファイル</th>
              <th>機器</th>
              <th>種別</th>
              <th>バージョン</th>
              <th>ポート</th>
              <th>隣接</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(snapshots ?? []).map((snapshot) => (
              <tr key={snapshot.id}>
                <td className="muted">{formatTimestamp(snapshot.created_at)}</td>
                <td>{snapshot.filename}</td>
                <td>
                  {snapshot.device_id ? (
                    <Link to={`/devices/${snapshot.device_id}`}>{snapshot.hostname}</Link>
                  ) : (
                    <span className="muted">{snapshot.hostname ?? "—"}</span>
                  )}
                </td>
                <td className="muted">{snapshot.vendor ?? "—"}</td>
                <td className="muted">
                  {snapshot.parsed_os_name ?? ""} {snapshot.parsed_os_version ?? ""}
                </td>
                <td>{snapshot.interface_count}</td>
                <td>{snapshot.neighbor_count}</td>
                <td>
                  <button onClick={() => setDiffTarget(snapshot.id)}>前回との差分</button>{" "}
                  <button className="danger" onClick={() => remove.mutate(snapshot.id)}>
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {diffTarget !== null && diff && (
        <>
          <h2>Config の差分</h2>
          {diff.changed ? (
            <pre className="diff">
              {diff.diff.split("\n").map((line, index) => (
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
          ) : (
            <div className="empty">比較できる前の世代がないか、内容に差がありません</div>
          )}
          <div className="actions">
            <button onClick={() => setDiffTarget(null)}>差分を閉じる</button>
          </div>
        </>
      )}
    </>
  );
}
