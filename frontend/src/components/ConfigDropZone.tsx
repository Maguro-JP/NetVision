import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api/client";
import type { IngestResult } from "../api/types";

/**
 * 機器の画面に Config を放り込むところ。
 *
 * 取り込み画面まで移動して、機器を選び直して、という往復が要らないように
 * する。見ている機器は決まっているので、その機器宛てとして読み込む
 * （ホスト名が読めない show 出力でも取り違えない）。
 *
 * 読み取った型番やバージョンを台帳へ入れるかどうかは、その場で選ばせる。
 * 上書きは戻せないので、押す前に何が起きるか分かる位置に置く。
 */
export default function ConfigDropZone({
  deviceId,
  hostname,
}: {
  deviceId: number;
  hostname: string;
}) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [applyInventory, setApplyInventory] = useState(true);
  const [results, setResults] = useState<IngestResult[] | null>(null);

  const send = useMutation({
    mutationFn: (files: File[]) => api.uploadConfigs(files, { deviceId, applyInventory }),
    onSuccess: (data) => {
      setResults(data);
      // 台帳・ポート・結線・世代のどれもが変わりうる
      for (const key of ["device", "devices", "interfaces", "configs", "topology"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  const take = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length) send.mutate(files);
  };

  return (
    <div className="config-drop">
      <div
        className={`dropzone${over ? " over" : ""}${send.isPending ? " busy" : ""}`}
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
        {send.isPending ? (
          <span>取り込んでいます…</span>
        ) : (
          <span>
            この機器（{hostname}）の Config をここに<strong>ドラッグ＆ドロップ</strong>
            <br />
            <span className="muted">クリックしてファイルを選ぶこともできます</span>
          </span>
        )}
      </div>

      <label className="drop-option">
        <input
          type="checkbox"
          checked={applyInventory}
          onChange={(e) => setApplyInventory(e.target.checked)}
        />{" "}
        読み取った型番・バージョン・シリアルを台帳へ反映する
      </label>

      {send.error && <div className="error">{(send.error as Error).message}</div>}

      {results?.map((result, index) => (
        <div key={index} className="drop-result">
          <strong>{result.filename}</strong> <span className="muted">{result.vendor_label}</span>
          <ul>
            <li>
              ポート {result.interface_count} / VLAN {result.vlan_count} / 隣接{" "}
              {result.neighbor_count}
            </li>
            {result.applied_fields.length > 0 && (
              <li>台帳へ反映: {result.applied_fields.join(" / ")}</li>
            )}
            {result.links_created > 0 && <li>結線 {result.links_created} 本を追加しました</li>}
            {result.aps_registered > 0 && (
              <li>コントローラの一覧から AP {result.aps_registered} 台を台帳に登録しました</li>
            )}
            {result.unchanged && <li className="muted">前回と同じ内容のため世代は増えていません</li>}
            {result.message && <li className="muted">{result.message}</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}
