import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Device } from "../api/types";
import { DEVICE_STATUS_LABEL, DEVICE_TYPE_LABEL } from "../lib/format";

type FormState = Record<string, string>;

const EMPTY: FormState = {
  hostname: "",
  vendor: "",
  model: "",
  serial_number: "",
  device_type: "switch",
  status: "active",
  os_name: "",
  os_version: "",
  firmware_version: "",
  site_name: "",
  rack: "",
  rack_unit: "",
  mgmt_ip: "",
  install_date: "",
  eos_date: "",
  eol_date: "",
  notes: "",
};

function toForm(device: Device): FormState {
  const form: FormState = { ...EMPTY };
  for (const key of Object.keys(EMPTY)) {
    const value = (device as unknown as Record<string, unknown>)[key];
    form[key] = value === null || value === undefined ? "" : String(value);
  }
  return form;
}

/** 空文字は「未入力」= null として送る。API 側の日付・数値型に合わせる。 */
function toPayload(form: FormState): Partial<Device> {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (key === "site_name") {
      // 空は「未設定に戻す」。null ではなく空文字で送る（未指定と区別する）
      payload[key] = value;
    } else {
      payload[key] = value === "" ? null : value;
    }
  }
  // 必須項目は null を許さないので除去（未入力なら API 側で 422 になる）
  for (const key of ["hostname", "vendor", "model", "device_type", "status"]) {
    if (payload[key] === null) delete payload[key];
  }
  return payload as Partial<Device>;
}

export default function DeviceFormPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);

  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: api.listSites });
  const { data: device } = useQuery({
    queryKey: ["device", id],
    queryFn: () => api.getDevice(Number(id)),
    enabled: isEdit,
  });

  useEffect(() => {
    if (device) setForm(toForm(device));
  }, [device]);

  const save = useMutation({
    mutationFn: (payload: Partial<Device>) =>
      isEdit ? api.updateDevice(Number(id), payload) : api.createDevice(payload),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
      queryClient.invalidateQueries({ queryKey: ["device", String(saved.id)] });
      navigate(`/devices/${saved.id}`);
    },
  });

  const set = (key: string) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const text = (key: keyof typeof EMPTY, label: string, type = "text") => (
    <div className="field">
      <label htmlFor={key}>{label}</label>
      <input id={key} type={type} value={form[key]} onChange={set(key)} />
    </div>
  );

  return (
    <>
      <h1>{isEdit ? "機器を編集" : "機器を追加"}</h1>
      <p className="subtitle">
        <Link to={isEdit ? `/devices/${id}` : "/devices"}>← 戻る</Link>
      </p>

      {save.error && <div className="error">{(save.error as Error).message}</div>}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(toPayload(form));
        }}
      >
        <div className="form-grid">
          {text("hostname", "ホスト名 *")}
          {text("vendor", "ベンダー *")}
          {text("model", "型番 *")}
          {text("serial_number", "シリアル番号")}

          <div className="field">
            <label htmlFor="device_type">種別</label>
            <select id="device_type" value={form.device_type} onChange={set("device_type")}>
              {Object.entries(DEVICE_TYPE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="status">状態</label>
            <select id="status" value={form.status} onChange={set("status")}>
              {Object.entries(DEVICE_STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          {text("os_name", "OS 名")}
          {text("os_version", "OS バージョン")}
          {text("firmware_version", "ファームウェアバージョン")}

          <div className="field">
            <label htmlFor="site_name">拠点</label>
            {/* 選ぶだけでなく打てるようにする。台帳に無い名前ならその場で作る
                （拠点を先に登録しないと機器を登録できないのでは手が止まる）。
                打つと部分一致で候補が出る */}
            <input
              id="site_name"
              list="site-candidates"
              value={form.site_name}
              onChange={set("site_name")}
              placeholder="拠点名（打つと候補が出ます。無い名前はそのまま作ります）"
              autoComplete="off"
            />
            <datalist id="site-candidates">
              {(sites ?? [])
                .filter((s) =>
                  form.site_name.trim()
                    ? s.name.toLowerCase().includes(form.site_name.trim().toLowerCase())
                    : true,
                )
                .slice(0, 50)
                .map((s) => (
                  <option key={s.id} value={s.name} />
                ))}
            </datalist>
          </div>
          {text("rack", "ラック")}
          {text("rack_unit", "搭載U")}
          {text("mgmt_ip", "管理IP")}

          {text("install_date", "導入日", "date")}
          {text("eos_date", "販売終了 (EoS)", "date")}
          {text("eol_date", "サポート終了 (EoL)", "date")}

          <div className="field wide">
            <label htmlFor="notes">備考</label>
            <textarea id="notes" rows={3} value={form.notes} onChange={set("notes")} />
          </div>
        </div>

        <div className="actions">
          <button className="primary" type="submit" disabled={save.isPending}>
            {save.isPending ? "保存中…" : "保存"}
          </button>
          <button type="button" onClick={() => navigate(-1)}>
            キャンセル
          </button>
        </div>
      </form>
    </>
  );
}
