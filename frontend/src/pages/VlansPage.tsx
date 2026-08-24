import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { TopologyVlan, VlanCatalogWrite } from "../api/types";

/** 表の1行分の編集内容。保存するまでは画面の中だけに持つ。 */
type Draft = VlanCatalogWrite;

const FIELDS: { key: keyof Draft; label: string; width: number; placeholder?: string }[] = [
  { key: "name", label: "名称", width: 160, placeholder: "全社ユーザー" },
  { key: "purpose", label: "用途", width: 240, placeholder: "社員端末（有線）" },
  { key: "owner", label: "管理", width: 120, placeholder: "情シス" },
  { key: "subnet", label: "サブネット", width: 140, placeholder: "10.20.30.0/24" },
];

export default function VlansPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [onlyUnregistered, setOnlyUnregistered] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const { data: vlans, isLoading } = useQuery({
    queryKey: ["topologyVlans"],
    queryFn: () => api.listTopologyVlans(),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["topologyVlans"] });
    queryClient.invalidateQueries({ queryKey: ["vlanCatalog"] });
  };

  const save = useMutation({
    mutationFn: ({ vlanId, body }: { vlanId: number; body: Draft }) =>
      api.saveVlanCatalog(vlanId, body),
    onSuccess: (_, variables) => {
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[variables.vlanId];
        return next;
      });
      setNotice(`VLAN ${variables.vlanId} を保存しました`);
      refresh();
    },
  });

  const adopt = useMutation({
    mutationFn: () => api.adoptObservedVlans(false),
    onSuccess: (result) => {
      setNotice(
        `実機の VLAN 名を台帳に取り込みました（新規 ${result.created} / 補完 ${result.updated}）。` +
          "既に書いてある行は変更していません。",
      );
      refresh();
    },
  });

  const rows = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    return (vlans ?? []).filter((vlan) => {
      if (onlyUnregistered && vlan.in_catalog) return false;
      if (!keyword) return true;
      const haystack = [
        String(vlan.vlan_id),
        vlan.name,
        vlan.purpose,
        vlan.owner,
        vlan.subnet,
        ...vlan.observed_names,
        ...vlan.device_names,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(keyword);
    });
  }, [vlans, filter, onlyUnregistered]);

  const unregistered = (vlans ?? []).filter((v) => !v.in_catalog).length;

  const edit = (vlan: TopologyVlan, key: keyof Draft, value: string) => {
    setDrafts((prev) => ({
      ...prev,
      [vlan.vlan_id]: {
        ...currentValues(vlan),
        ...prev[vlan.vlan_id],
        [key]: value === "" ? null : value,
      },
    }));
  };

  return (
    <>
      <h1>VLAN</h1>
      <p className="subtitle">
        番号だけでは何のセグメントか分かりません。名称と用途はここで管理します。
        <strong>ここに書いた内容は Config を取り込み直しても上書きされません。</strong>
        実機に書かれている名前とは別に持つので、食い違っていれば機器側の設定が古いということです。
      </p>

      <div className="toolbar">
        <input
          type="search"
          placeholder="番号・名称・用途・機器名で絞り込む"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <label className="vlan-chip">
          <input
            type="checkbox"
            checked={onlyUnregistered}
            onChange={(e) => setOnlyUnregistered(e.target.checked)}
          />{" "}
          台帳に無いものだけ{unregistered > 0 && <strong> （{unregistered}件）</strong>}
        </label>
        <button
          onClick={() => adopt.mutate()}
          disabled={adopt.isPending}
          title="実機の Config に書かれている VLAN 名を、台帳の下書きとして取り込みます。既に書いてある行は触りません"
        >
          実機の名前で下書きを作る
        </button>
      </div>

      {notice && (
        <div className="card" style={{ borderColor: "var(--accent)", marginBottom: 12 }}>
          {notice}
        </div>
      )}
      {(save.error || adopt.error) && (
        <div className="error">{((save.error ?? adopt.error) as Error).message}</div>
      )}

      {isLoading ? (
        <p className="muted">読み込み中…</p>
      ) : rows.length === 0 ? (
        <div className="empty">
          表示できる VLAN がありません。Config を取り込むか、行を追加してください。
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 70 }}>VLAN</th>
                {FIELDS.map((field) => (
                  <th key={field.key} style={{ width: field.width }}>
                    {field.label}
                  </th>
                ))}
                <th>載っている機器</th>
                <th style={{ width: 150 }}>ポート</th>
                <th style={{ width: 90 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((vlan) => {
                const draft = drafts[vlan.vlan_id];
                const values = { ...currentValues(vlan), ...draft };
                return (
                  <tr key={vlan.vlan_id}>
                    <td>
                      <strong>{vlan.vlan_id}</strong>
                      {!vlan.in_catalog && (
                        <div className="badge unknown" title="台帳に未登録のセグメントです">
                          未登録
                        </div>
                      )}
                    </td>
                    {FIELDS.map((field) => (
                      <td key={field.key}>
                        <input
                          value={(values[field.key] as string | null) ?? ""}
                          placeholder={field.placeholder}
                          onChange={(e) => edit(vlan, field.key, e.target.value)}
                          style={{ width: "100%" }}
                        />
                        {field.key === "name" &&
                          vlan.observed_names.length > 0 &&
                          !vlan.observed_names.includes(values.name ?? "") && (
                            <div
                              className="muted"
                              style={{ fontSize: 11 }}
                              title="実機の Config に書かれている名前です"
                            >
                              実機: {vlan.observed_names.join(" / ")}
                            </div>
                          )}
                      </td>
                    ))}
                    <td>
                      <span title={vlan.device_names.join("\n")}>
                        {vlan.device_names.length}台
                      </span>
                      {vlan.device_ids.length > 0 && vlan.defined_device_ids.length === 0 && (
                        <div
                          className="badge warning"
                          title="どの機器にも VLAN が定義されていません。トランクで通しているだけの状態です"
                        >
                          定義なし
                        </div>
                      )}
                    </td>
                    <td className="muted num" style={{ fontSize: 12 }}>
                      アクセス {vlan.access_port_count} / トランク {vlan.trunk_port_count}
                      {vlan.native_port_count > 0 && ` / ネイティブ ${vlan.native_port_count}`}
                    </td>
                    <td>
                      <button
                        className="primary"
                        disabled={!draft || save.isPending}
                        onClick={() => save.mutate({ vlanId: vlan.vlan_id, body: values })}
                      >
                        保存
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <h2>読み方</h2>
      <ul className="muted">
        <li>
          <strong>未登録</strong> … 実機には居るのに台帳に無い VLAN です。素性の分からない
          セグメントなので、用途を書くか、使っていないなら消す判断をしてください
        </li>
        <li>
          <strong>定義なし</strong> … どの機器にも VLAN 定義が無いのに、トランクの許可には
          残っている状態です。消し忘れであることが多いところです
        </li>
        <li>
          <strong>実機:</strong> … Config に書かれている名前です。台帳と違っていれば、
          機器側の設定が古いか、台帳の方が新しいかのどちらかです
        </li>
      </ul>
    </>
  );
}

function currentValues(vlan: TopologyVlan): Draft {
  return {
    name: vlan.in_catalog ? vlan.name : null,
    purpose: vlan.purpose,
    owner: vlan.owner,
    subnet: vlan.subnet,
  };
}
