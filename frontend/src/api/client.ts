import type {
  DashboardSummary,
  Device,
  DeviceDetail,
  ChangeLogPage,
  ConfigDiff,
  ConfigSnapshot,
  ImportField,
  IngestResult,
  InsightReport,
  ImportResult,
  MaintenanceContract,
  NetworkInterface,
  RebuildResult,
  Scenario,
  ScenarioDetail,
  ScenarioItem,
  ScenarioSummary,
  Site,
  TopologyEdge,
  TopologyGraph,
  TopologyStats,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** multipart 送信。Content-Type はブラウザに境界文字列ごと決めさせる。 */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }
  return (await res.json()) as T;
}

/** FastAPI のエラー形式（detail が文字列 or バリデーション配列）を読める文字列にする。 */
async function extractError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d: { loc?: (string | number)[]; msg?: string }) => {
          const field = d.loc?.filter((p) => p !== "body").join(".");
          return field ? `${field}: ${d.msg}` : d.msg;
        })
        .join(" / ");
    }
  } catch {
    /* JSON でない場合は下のフォールバックへ */
  }
  return `${res.status} ${res.statusText}`;
}

function query(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== "" && v !== null,
  );
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
}

export interface ImportOptions {
  file: File;
  keyField: "hostname" | "serial_number";
  updateExisting: boolean;
  /** 自動判別を上書きする {列名: 項目名}。空文字はその列を取り込まない。 */
  mapping?: Record<string, string>;
  commit: boolean;
}

export interface ChangeFilters extends Record<string, string | number | undefined> {
  entity_type?: string;
  entity_id?: number;
  action?: string;
  source?: string;
  batch_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface DeviceFilters extends Record<string, string | number | undefined> {
  q?: string;
  vendor?: string;
  device_type?: string;
  status?: string;
  site_id?: number;
  severity?: string;
}

export const api = {
  summary: () => request<DashboardSummary>("/summary"),
  alerts: (withinDays: number) => request<Device[]>(`/alerts${query({ within_days: withinDays })}`),

  listDevices: (filters: DeviceFilters = {}) => request<Device[]>(`/devices${query(filters)}`),
  getDevice: (id: number) => request<DeviceDetail>(`/devices/${id}`),
  createDevice: (body: Partial<Device>) =>
    request<DeviceDetail>("/devices", { method: "POST", body: JSON.stringify(body) }),
  updateDevice: (id: number, body: Partial<Device>) =>
    request<DeviceDetail>(`/devices/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteDevice: (id: number) => request<void>(`/devices/${id}`, { method: "DELETE" }),

  createContract: (deviceId: number, body: Partial<MaintenanceContract>) =>
    request<MaintenanceContract>(`/devices/${deviceId}/contracts`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteContract: (id: number) => request<void>(`/contracts/${id}`, { method: "DELETE" }),

  /** 書き出しURL。一覧の絞り込みをそのまま引き継いで出力する。 */
  exportUrl: (
    format: "csv" | "xlsx",
    filters: DeviceFilters = {},
    options: { includeStatus?: boolean; encoding?: "utf-8" | "shift_jis" } = {},
  ) => {
    const params: Record<string, string | number | undefined> = { ...filters };
    if (options.includeStatus === false) params.include_status = "false";
    if (format === "csv" && options.encoding) params.encoding = options.encoding;
    return `${BASE}/export/devices.${format}${query(params)}`;
  },

  importFields: () => request<ImportField[]>("/import/fields"),
  importTemplateUrl: `${BASE}/import/template.csv`,
  importDevices: (options: ImportOptions) => {
    const form = new FormData();
    form.append("file", options.file);
    form.append("key_field", options.keyField);
    form.append("update_existing", String(options.updateExisting));
    if (options.mapping) form.append("mapping", JSON.stringify(options.mapping));
    return upload<ImportResult>(
      options.commit ? "/import/devices" : "/import/devices/preview",
      form,
    );
  },

  /** エラー行だけを抽出したCSVを取得する（Blob で受けてブラウザに保存させる）。 */
  importErrorsCsv: async (options: ImportOptions & { encoding?: "utf-8" | "shift_jis" }) => {
    const form = new FormData();
    form.append("file", options.file);
    form.append("key_field", options.keyField);
    form.append("update_existing", String(options.updateExisting));
    form.append("encoding", options.encoding ?? "utf-8");
    if (options.mapping) form.append("mapping", JSON.stringify(options.mapping));

    const res = await fetch(`${BASE}/import/devices/errors.csv`, { method: "POST", body: form });
    if (!res.ok) throw new ApiError(res.status, await extractError(res));
    return res.blob();
  },

  listChanges: (params: ChangeFilters = {}) =>
    request<ChangeLogPage>(`/changes${query(params)}`),

  listInsights: (params: { category?: string; site_id?: number } = {}) =>
    request<InsightReport>(`/insights${query(params)}`),

  listScenarios: () => request<Scenario[]>("/scenarios"),
  getScenario: (id: number) => request<ScenarioDetail>(`/scenarios/${id}`),
  createScenario: (body: Partial<Scenario>) =>
    request<ScenarioDetail>("/scenarios", { method: "POST", body: JSON.stringify(body) }),
  updateScenario: (id: number, body: Partial<Scenario>) =>
    request<ScenarioDetail>(`/scenarios/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteScenario: (id: number) => request<void>(`/scenarios/${id}`, { method: "DELETE" }),
  generateScenarioItems: (id: number, withinDays: number) =>
    request<ScenarioDetail>(`/scenarios/${id}/generate?within_days=${withinDays}`, {
      method: "POST",
    }),
  scenarioSummary: (id: number) => request<ScenarioSummary>(`/scenarios/${id}/summary`),
  addScenarioItem: (scenarioId: number, body: Partial<ScenarioItem>) =>
    request<ScenarioItem>(`/scenarios/${scenarioId}/items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateScenarioItem: (itemId: number, body: Partial<ScenarioItem>) =>
    request<ScenarioItem>(`/scenarios/items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteScenarioItem: (itemId: number) =>
    request<void>(`/scenarios/items/${itemId}`, { method: "DELETE" }),

  // --- Config 取り込み ---
  uploadConfigs: (files: File[], options: { deviceId?: number; applyInventory?: boolean } = {}) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    if (options.deviceId !== undefined) form.append("device_id", String(options.deviceId));
    form.append("apply_inventory", String(options.applyInventory ?? false));
    return upload<IngestResult[]>("/configs/upload", form);
  },
  previewConfig: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return upload<IngestResult>("/configs/preview", form);
  },
  listConfigs: (deviceId?: number) =>
    request<ConfigSnapshot[]>(`/configs${query({ device_id: deviceId })}`),
  getConfig: (id: number) => request<ConfigSnapshot & { content: string }>(`/configs/${id}`),
  diffConfig: (id: number, against?: number) =>
    request<ConfigDiff>(`/configs/${id}/diff${query({ against })}`),
  deleteConfig: (id: number) => request<void>(`/configs/${id}`, { method: "DELETE" }),
  listInterfaces: (deviceId: number) =>
    request<NetworkInterface[]>(`/configs/devices/${deviceId}/interfaces`),

  // --- トポロジ ---
  getTopology: (layer: string, includeRetired = false) =>
    request<TopologyGraph>(`/topology${query({ layer, include_retired: String(includeRetired) })}`),
  getTopologyStats: (layer: string) => request<TopologyStats>(`/topology/stats${query({ layer })}`),
  savePositions: (layer: string, positions: { device_id: number; x: number; y: number }[]) =>
    request<{ saved: number }>("/topology/positions", {
      method: "PUT",
      body: JSON.stringify({ layer, positions }),
    }),
  resetPositions: (layer: string) =>
    request<{ reset: number }>(`/topology/positions${query({ layer })}`, { method: "DELETE" }),
  rebuildTopology: () => request<RebuildResult>("/topology/rebuild", { method: "POST" }),
  createLink: (body: {
    a_device_id: number;
    b_device_id: number;
    a_interface?: string;
    b_interface?: string;
    label?: string | null;
  }) => request<TopologyEdge>("/topology/links", { method: "POST", body: JSON.stringify(body) }),
  updateLink: (id: number, body: { a_interface?: string; b_interface?: string; label?: string }) =>
    request<TopologyEdge>(`/topology/links/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteLink: (id: number) => request<void>(`/topology/links/${id}`, { method: "DELETE" }),

  listSites: () => request<Site[]>("/sites"),
  createSite: (body: Partial<Site>) =>
    request<Site>("/sites", { method: "POST", body: JSON.stringify(body) }),
  deleteSite: (id: number) => request<void>(`/sites/${id}`, { method: "DELETE" }),
};
