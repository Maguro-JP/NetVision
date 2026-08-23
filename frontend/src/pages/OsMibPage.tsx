import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import type { MibReport, ModelSpread } from "../api/types";

/**
 * OS の版と、SNMP で取った値。
 *
 * Config は「どう設定してあるか」で、SNMP は「いまどうなっているか」。
 * 設定では使うことになっているのに落ちているポート、台帳と違う版、
 * 入れ替わっている号機。どれも設定ファイルだけを見ていても出てこない。
 *
 * この道具は外へ通信しないので、機器へ取りには行かない。人が snmpwalk で
 * 取ってきた出力を持ち込む。読むだけで、台帳には何も書かない。
 */

/** その機種の揃い方を一言で。台数だけでは、まずいのかどうか分からない。 */
export function spreadOf(spread: ModelSpread): string {
  if (!spread.newest) return "版が入っていないので、比べられません";
  if (!spread.mixed)
    return `${spread.devices} 台とも ${spread.newest} で揃っています`;
  return `${spread.behind} 台が ${spread.newest} より古い版のままです`;
}

/** 読めた割合を一言で。全部読めたと思われないように。 */
export function coverageOf(report: MibReport): string {
  if (report.lines === 0) return "";
  if (report.unknown === 0) return `${report.read} 件を読みました`;
  return `${report.read} 件のうち、意味を持っていない値が ${report.unknown} 件ありました`;
}

export default function OsMibPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [text, setText] = useState("");
  const [report, setReport] = useState<MibReport | null>(null);

  const { data: os } = useQuery({
    queryKey: ["osReport"],
    queryFn: api.osReport,
  });

  const drop = useMutation({
    mutationFn: (files: File[]) => api.readMibFiles(files),
    onSuccess: setReport,
  });
  const paste = useMutation({
    mutationFn: () => api.readMib(text),
    onSuccess: setReport,
  });

  const take = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length) drop.mutate(files);
  };

  return (
    <>
      <h1>版と、いまの状態</h1>
      <p className="subtitle">
        同じ機種で OS の版が揃っているかを数えます。あわせて、snmpwalk
        の出力を放り込むと、いまの状態（落ちているポート、台帳との食い違い）を出します。
        機器へ取りには行きません。台帳には何も書きません。
      </p>

      <section>
        <h2>OS の版の散らばり</h2>
        {os?.notes.map((note) => (
          <p className="notice" key={note}>
            {note}
          </p>
        ))}
        <table>
          <thead>
            <tr>
              <th>機種</th>
              <th>台数</th>
              <th>動いている版</th>
              <th>揃い方</th>
            </tr>
          </thead>
          <tbody>
            {(os?.models ?? []).map((spread) => (
              <tr key={`${spread.model}-${spread.os_name}`}>
                <td>{spread.model}</td>
                <td>{spread.devices}</td>
                <td>
                  {spread.versions.map((item) => (
                    <div key={item.version} title={item.hostnames.join("、")}>
                      {item.version} × {item.count}
                      {item.newest && (
                        <span className="muted">（いちばん新しい）</span>
                      )}
                    </div>
                  ))}
                  {spread.unknown > 0 && (
                    <div className="muted">版が未記入 × {spread.unknown}</div>
                  )}
                </td>
                <td>
                  {spreadOf(spread)}
                  <div className="muted">{spread.basis}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>SNMP の出力を読む</h2>
        <div className="config-drop">
          <div
            className={`dropzone${over ? " over" : ""}${drop.isPending ? " busy" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              take(e.dataTransfer.files);
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
              type="file"
              multiple
              hidden
              onChange={(e) => {
                take(e.target.files);
                e.target.value = "";
              }}
            />
            {drop.isPending ? (
              <span>読んでいます…</span>
            ) : (
              <span>
                ここに snmpwalk の出力を放り込む
                <br />
                <small className="muted">
                  名前の形（SNMPv2-MIB::sysName.0）でも、数字の形（.1.3.6.1.2.1.1.5.0）でも
                  読めます
                </small>
              </span>
            )}
          </div>
        </div>
        {drop.error && (
          <div className="error">{(drop.error as Error).message}</div>
        )}

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          spellCheck={false}
          style={{ width: "100%", fontFamily: "monospace" }}
          placeholder="SNMPv2-MIB::sysName.0 = STRING: core-sw-01"
        />
        <div className="actions">
          <button
            className="primary"
            disabled={!text.trim() || paste.isPending}
            onClick={() => paste.mutate()}
          >
            {paste.isPending ? "読んでいます…" : "貼り付けた分を読む"}
          </button>
        </div>
        {paste.error && (
          <div className="error">{(paste.error as Error).message}</div>
        )}
      </section>

      {report && (
        <>
          <section>
            <h2>読めたこと</h2>
            <p className="muted">{coverageOf(report)}</p>
            <ul>
              <li>名前：{report.hostname ?? "読めませんでした"}</li>
              <li>機種：{report.model ?? "読めませんでした"}</li>
              <li>OS の版：{report.os_version ?? "読めませんでした"}</li>
              {report.serial && <li>製造番号：{report.serial}</li>}
              {report.location && <li>設置場所：{report.location}</li>}
              {report.uptime && (
                <li>電源が入ってからの時間：{report.uptime}</li>
              )}
            </ul>
          </section>

          {report.differences.length > 0 && (
            <section>
              <h2>台帳との食い違い</h2>
              <p className="muted">
                どちらかが古いということです。実機に合わせるか、台帳を直すかを決めてください。
              </p>
              <ul>
                {report.differences.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </section>
          )}

          {report.ports.length > 0 && (
            <section>
              <h2>ポートの状態</h2>
              <table>
                <thead>
                  <tr>
                    <th>ポート</th>
                    <th>説明</th>
                    <th>状態</th>
                    <th>速さ</th>
                  </tr>
                </thead>
                <tbody>
                  {report.ports.map((port) => (
                    <tr key={port.index}>
                      <td>{port.name || port.index}</td>
                      <td>{port.alias || "—"}</td>
                      <td
                        style={{
                          color: port.down_but_enabled
                            ? "var(--danger)"
                            : undefined,
                        }}
                      >
                        {port.state}
                      </td>
                      <td>
                        {port.speed_mbps ? `${port.speed_mbps} Mbps` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {report.notes.map((note) => (
            <p className="muted" key={note}>
              {note}
            </p>
          ))}
        </>
      )}
    </>
  );
}
