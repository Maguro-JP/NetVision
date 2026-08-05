import type {
  DashboardSummary,
  Device,
  DeviceDetail,
  MaintenanceContract,
  Site,
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

  listSites: () => request<Site[]>("/sites"),
  createSite: (body: Partial<Site>) =>
    request<Site>("/sites", { method: "POST", body: JSON.stringify(body) }),
  deleteSite: (id: number) => request<void>(`/sites/${id}`, { method: "DELETE" }),
};
