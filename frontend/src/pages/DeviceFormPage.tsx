import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Device } from "../api/types";
import { DEVICE_STATUS_LABEL, DEVICE_TYPE_LABEL } from "../lib/format";
import { confirmLeave, useUnsavedWarning } from "../lib/unsaved";

type FormState = Record<string, string>;

const EMPTY: FormState = {
  hostname: "",
  label: "",
  code: "",
  vendor: "",
  model: "",
  serial_number: "",
  device_type: "switch",
  status: "active",
  os_name: "",
  os_version: "",
  firmware_version: "",
  site_name: "",
  site_code: "",
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
    if (key === "site_code") continue;
    const value = (device as unknown as Record<string, unknown>)[key];
    form[key] = value === null || value === undefined ? "" : String(value);
  }
  return form;
}

/** 空文字は「未入力」= null として送る。API 側の日付・数値型に合わせる。 */
function toPayload(form: FormState): Partial<Device> & { site_code?: string | null } {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (key === "site_name" || key === "site_code") {
      // 空は「未設定に戻す」。null ではなく空文字で送る（未指定と区別する）
      payload[key] = value;
    } else if (key === "code") {
      payload[key] = value.trim() === "" ? null : value.trim();
    } else {
      payload[key] = value === "" ? null : value;
    }
  }
  // 必須項目は null を許さないので除去（未入力なら API 側で 422 になる）
  for (const key of ["hostname", "vendor", "model", "device_type", "status"]) {
    if (payload[key] === null) delete payload[key];
  }
  return payload as Partial<Device> & { site_code?: string | null };
}

export default function DeviceFormPage() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [baseline, setBaseline] = useState<FormState>(EMPTY);
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  useUnsavedWarning(dirty);

  const { data: sites } = useQuery({ queryKey: ["sites"], queryFn: api.listSites });
  const { data: device } = useQuery({
    queryKey: ["device", id],
    queryFn: () => api.getDevice(Number(id)),
    enabled: isEdit,
  });

  useEffect(() => {
    if (device) {
      const next = toForm(device);
      const matched = (sites ?? []).find((s) => s.id === device.site_id);
      if (matched?.code) next.site_code = matched.code;
      setForm(next);
      setBaseline(next);
    }
  }, [device, sites]);

  const save = useMutation({
    mutationFn: (payload: Partial<Device> & { site_code?: string | null }) =>
      isEdit ? api.updateDevice(Number(id), payload) : api.createDevice(payload),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      queryClient.invalidateQueries({ queryKey: ["sites"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
      queryClient.invalidateQueries({ queryKey: ["device", String(saved.id)] });
      navigate(`/devices/${saved.id}`);
    },
  });

  const set = (key: string) => (e: { target: { value: string } }) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const text = (key: keyof typeof EMPTY, label: string, type = "text", extra: { required?: boolean; autoFocus?: boolean } = {}) => (
    <div className="field">
      <label htmlFor={key}>{label}</label>
      <input
        id={key}
        type={type}
        value={form[key]}
        onChange={set(key)}
        required={extra.required}
        autoFocus={extra.autoFocus}
      />
    </div>
  );

  return (
    <>
      <h1>{isEdit ? "機器を編集" : "機器を追加"}</h1>
      <p className="subtitle">
        <Link
          to={isEdit ? `/devices/${id}` : "/devices"}
          onClick={(event) => {
            if (!confirmLeave(dirty)) event.preventDefault();
          }}
        >
          ← 戻る
        </Link>
      </p>

      {save.error && <div className="error">{(save.error as Error).message}</div>}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(toPayload(form));
        }}
      >
        <div className="form-grid">
          {text("hostname", "ホスト名 *", "text", { required: true, autoFocus: true })}
          <div className="field">
            <label htmlFor="label">機器名</label>
            <input
              id="label"
              value={form.label}
              onChange={set("label")}
              placeholder="現場での呼び名（任意）"
              title="「本社コア1号機」など、人が付けた名前。空ならホスト名で呼びます"
            />
          </div>
          <div className="field">
            <label htmlFor="code">管理ID</label>
            <input
              id="code"
              value={form.code}
              onChange={set("code")}
              placeholder="社内台帳の番号など（任意・一意）"
              title="プロジェクトで使っている機器の番号。空でも登録できます"
            />
          </div>
          {/* ベンダーと型番は Config を読ませれば埋まる。台帳を起こす時点で
              必須にすると、名前だけ分かっている機器を登録できない */}
          {text("vendor", "ベンダー")}
          {text("model", "型番")}
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
            {/* 取り込みの CSV でも同じ言葉を書く。ここに並べておけば、
                何を書けばよいのかを台帳の画面だけで確かめられる */}
            <span className="hint muted">
              取り込みでは {Object.values(DEVICE_TYPE_LABEL).join(" / ")} と書きます
            </span>
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
            <label htmlFor="site_code">拠点ID</label>
            <input
              id="site_code"
              list="site-code-candidates"
              value={form.site_code}
              onChange={(e) => {
                const code = e.target.value;
                const matched = (sites ?? []).find((s) => s.code === code);
                setForm((prev) => ({
                  ...prev,
                  site_code: code,
                  site_name: matched ? matched.name : prev.site_name,
                }));
              }}
              placeholder="拠点台帳の拠点ID（任意）"
              title="先に拠点台帳へ登録した拠点ID。あれば名前よりこちらを使います"
              autoComplete="off"
            />
            <datalist id="site-code-candidates">
              {(sites ?? [])
                .filter((s) => s.code)
                .slice(0, 50)
                .map((s) => (
                  <option key={s.id} value={s.code!}>
                    {s.name}
                  </option>
                ))}
            </datalist>
          </div>

          <div className="field">
            <label htmlFor="site_name">拠点名</label>
            {/* 選ぶだけでなく打てるようにする。台帳に無い名前ならその場で作る
                （拠点を先に登録しないと機器を登録できないのでは手が止まる）。
                打つと部分一致で候補が出る */}
            <input
              id="site_name"
              list="site-candidates"
              value={form.site_name}
              onChange={(e) => {
                const name = e.target.value;
                const matched = (sites ?? []).find(
                  (s) => s.name.toLowerCase() === name.toLowerCase(),
                );
                setForm((prev) => ({
                  ...prev,
                  site_name: name,
                  site_code: matched?.code ?? "",
                }));
              }}
              placeholder="拠点名（打つと候補が出ます。無い名前はそのまま作ります）"
              autoComplete="off"
            />
            <datalist id="site-candidates">
              {(sites ?? [])
                .filter((s) =>
                  form.site_name.trim()
                    ? s.name.toLowerCase().includes(form.site_name.trim().toLowerCase()) ||
                      (s.code ?? "").toLowerCase().includes(form.site_name.trim().toLowerCase())
                    : true,
                )
                .slice(0, 50)
                .map((s) => (
                  <option key={s.id} value={s.name}>
                    {s.code ? `${s.code} — ${s.name}` : s.name}
                  </option>
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
          <button
            type="button"
            onClick={() => {
              if (confirmLeave(dirty)) navigate(-1);
            }}
          >
            キャンセル
          </button>
        </div>
      </form>
    </>
  );
}
