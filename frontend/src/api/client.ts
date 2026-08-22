import type {
  AdditionPlan,
  LinkReport,
  ConfigEdit,
  AskBackAnswer,
  ConfigCheck,
  ConfigTemplateDraft,
  HouseStyle,
  AskResult,
  GlossaryTerm,
  LinkCandidate,
  CapabilityIndex,
  Blueprint,
  BlueprintCondition,
  TestSite,
  L3Report,
  AppSettings,
  ChangeLog,
  LinkImportResult,
  SiteImportResult,
  TemplateDraft,
  SpaceView,
  DashboardSummary,
  Device,
  DeviceDetail,
  ChangeLogPage,
  ConfigDiff,
  ConfigDraft,
  ConfigPlan,
  ConfigTemplate,
  ConfigTemplateMatch,
  ConfigSnapshot,
  ImportField,
  IngestResult,
  InsightReport,
  ImportResult,
  Job,
  LlmStatus,
  MaintenanceContract,
  NetworkInterface,
  AppMeta,
  DeviceRemediation,
  ExposureRow,
  ExposureSummary,
  ExposureSyncResult,
  FixConfig,
  ImpactReport,
  ProductImportResult,
  PlanOutputs,
  ProductReference,
  RebuildResult,
  Remediation,
  ReplacementPlan,
  ReferenceFreshness,
  RenderResult,
  Scenario,
  ScenarioDetail,
  ScenarioItem,
  ScenarioSummary,
  SchemaStatus,
  Site,
  TopologyEdge,
  TopologyGraph,
  TopologyGroup,
  TopologyStats,
  TopologyVlan,
  TopologyVrf,
  VlanCatalogEntry,
  VlanCatalogWrite,
  VendorDocument,
  DocumentHit,
  VendorDocumentMatch,
  Vulnerability,
  VulnerabilityImportResult,
  VulnerabilitySummary,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";
/** 画面の読み込みが 10 秒以上止まらないようにする。取り込みは別で長く待つ */
const GET_TIMEOUT_MS = 8_000;
const LONG_TIMEOUT_MS = 120_000;
/** 台帳の大規模取り込み（16万行級）は数分かかることがある */
const IMPORT_TIMEOUT_MS = 30 * 60_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** 4xx は同じ内容を送り直しても直らない。待たせてストレスになるだけ。 */
export function isRetryableError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.status === 0) return true;
  return error.status >= 500;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

async function send(
  path: string,
  init: RequestInit = {},
  timeoutMs?: number,
): Promise<Response> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    throw new ApiError(0, "オフラインです。接続してからやり直してください");
  }

  const method = (init.method ?? "GET").toUpperCase();
  const wait =
    timeoutMs ?? (method === "GET" ? GET_TIMEOUT_MS : LONG_TIMEOUT_MS);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), wait);
  init.signal?.addEventListener("abort", () => controller.abort());

  try {
    return await fetch(`${BASE}${path}`, {
      ...init,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (isAbortError(error)) {
      throw new ApiError(
        0,
        "応答がありません。しばらくしてからもう一度試してください",
      );
    }
    throw new ApiError(
      0,
      "接続できませんでした。ネットワークを確認してください",
    );
  } finally {
    clearTimeout(timer);
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const res = await send(
    path,
    {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    },
    timeoutMs,
  );

  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/**
 * 一覧を1ページ分だけ取る。総数はヘッダから読む。
 *
 * 16万台の環境では全件を返せない。かといって総数が分からないとページも
 * 送れないので、本体はページ、総数はヘッダに分けて受け取る。
 */
async function requestWithTotal<T>(
  path: string,
): Promise<{ items: T[]; total: number }> {
  const res = await send(path, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }
  const items = (await res.json()) as T[];
  const header = res.headers.get("X-Total-Count");
  return { items, total: header === null ? items.length : Number(header) };
}

/** multipart 送信。Content-Type はブラウザに境界文字列ごと決めさせる。 */
async function upload<T>(
  path: string,
  form: FormData,
  timeoutMs?: number,
): Promise<T> {
  const res = await send(path, { method: "POST", body: form }, timeoutMs);
  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }
  return (await res.json()) as T;
}

export function humanizeHttpError(status: number, fallback: string): string {
  if (status === 404)
    return "見つかりませんでした。削除されたか、アドレスが違う可能性があります";
  if (status === 409)
    return "他の操作と衝突しました。画面を読み直してからやり直してください";
  if (status === 413)
    return "ファイルが大きすぎます。CSV に分けるか、サーバーの NETVISION_MAX_IMPORT_BYTES を確認してください";
  if (status >= 500)
    return "サーバーで問題が起きました。しばらくしてからやり直してください";
  return fallback;
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
  return humanizeHttpError(res.status, `${res.status} ${res.statusText}`);
}

function query(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const entries = Object.entries(params).filter(
    // false は「指定なし」と同じ扱い。?incomplete=false を送っても意味がない
    ([, v]) => v !== undefined && v !== "" && v !== null && v !== false,
  );
  return entries.length
    ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}`
    : "";
}

export type ImportKeyField = "code" | "hostname" | "serial_number";

export interface ImportOptions {
  file: File;
  keyField: ImportKeyField;
  updateExisting: boolean;
  /** 自動判別を上書きする {列名: 項目名}。空文字はその列を取り込まない。 */
  mapping?: Record<string, string>;
  commit: boolean;
}

export interface ChangeFilters extends Record<
  string,
  string | number | undefined
> {
  entity_type?: string;
  entity_id?: number;
  action?: string;
  source?: string;
  batch_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface DeviceFilters extends Record<
  string,
  string | number | boolean | undefined
> {
  q?: string;
  hostname?: string;
  vendor?: string;
  model?: string;
  serial_number?: string;
  os_version?: string;
  mgmt_ip?: string;
  rack?: string;
  notes?: string;
  product?: string;
  site_name?: string;
  maintenance?: string;
  eol?: string;
  device_type?: string;
  status?: string;
  site_id?: number;
  severity?: string;
  /** 埋まっていない項目がある機器だけ */
  incomplete?: boolean;
  limit?: number;
  offset?: number;
}

/** 1ページ分の機器と、条件に合う総数。 */
export interface DevicePage {
  items: Device[];
  total: number;
}

export const api = {
  summary: () => request<DashboardSummary>("/summary"),
  alerts: (withinDays: number) =>
    request<Device[]>(`/alerts${query({ within_days: withinDays })}`),

  listDevices: (filters: DeviceFilters = {}) =>
    request<Device[]>(`/devices${query(filters)}`),
  /** 1ページ分を、総数つきで取る。総数は X-Total-Count ヘッダで返ってくる。 */
  listDevicePage: (filters: DeviceFilters = {}) =>
    requestWithTotal<Device>(`/devices${query(filters)}`),
  getDevice: (id: number) => request<DeviceDetail>(`/devices/${id}`),
  createDevice: (body: Partial<Device>) =>
    request<DeviceDetail>("/devices", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateDevice: (id: number, body: Partial<Device>) =>
    request<DeviceDetail>(`/devices/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteDevice: (id: number) =>
    request<void>(`/devices/${id}`, { method: "DELETE" }),

  createContract: (deviceId: number, body: Partial<MaintenanceContract>) =>
    request<MaintenanceContract>(`/devices/${deviceId}/contracts`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteContract: (id: number) =>
    request<void>(`/contracts/${id}`, { method: "DELETE" }),

  /** 書き出しURL。一覧の絞り込みをそのまま引き継いで出力する。 */
  exportUrl: (
    format: "csv" | "xlsx",
    filters: DeviceFilters = {},
    options: { includeStatus?: boolean; encoding?: "utf-8" | "shift_jis" } = {},
  ) => {
    const params: Record<string, string | number | boolean | undefined> = {
      ...filters,
    };
    if (options.includeStatus === false) params.include_status = "false";
    if (format === "csv" && options.encoding)
      params.encoding = options.encoding;
    return `${BASE}/export/devices.${format}${query(params)}`;
  },

  /** 拠点台帳の書き出し。直してそのまま取り込み直せる並びで出る */
  siteExportUrl: (format: "csv" | "xlsx", encoding?: "utf-8" | "shift_jis") =>
    `${BASE}/export/sites.${format}${
      format === "csv" && encoding ? query({ encoding }) : ""
    }`,

  importFields: () => request<ImportField[]>("/import/fields"),
  importTemplateUrl: `${BASE}/import/template.csv`,

  /** 変更履歴1件を元の値に戻す。戻したことも履歴に残る。 */
  revertChange: (id: number) =>
    request<ChangeLog>(`/changes/${id}/revert`, { method: "POST" }),
  /** 取り込み1回分をまとめて戻す。 */
  revertBatch: (batchId: string) =>
    request<{ reverted: number; skipped: { id: number; reason: string }[] }>(
      `/changes/batches/${batchId}/revert`,
      { method: "POST" },
    ),

  /** 眺めるための、拠点単位にまとめた姿。機器が何台あっても大きさは変わらない。 */
  getSpaceView: () => request<SpaceView>("/topology/space"),

  /** 結線表のひな形。これに書いて取り込めば図が立ち上がる。 */
  linkTemplateUrl: `${BASE}/import/links/template.xlsx`,
  importLinks: (file: File, commit: boolean) => {
    const form = new FormData();
    form.append("file", file);
    return upload<LinkImportResult>(
      commit ? "/import/links" : "/import/links/preview",
      form,
    );
  },
  /** 拠点台帳の取り込み。書き出した表をそのまま直して戻せる。 */
  /** 資料の章から起こしたテンプレートの下書き。登録するかは人が選ぶ */
  templateDrafts: (documentId: number) =>
    request<TemplateDraft[]>(`/documents/${documentId}/template-drafts`),
  createTemplateFromDocument: (documentId: number, index: number) =>
    request<ConfigTemplate>(`/documents/${documentId}/template-drafts`, {
      method: "POST",
      body: JSON.stringify({ index }),
    }),
  importSites: (file: File, commit: boolean) => {
    const form = new FormData();
    form.append("file", file);
    return upload<SiteImportResult>(
      commit ? "/import/sites" : "/import/sites/preview",
      form,
      IMPORT_TIMEOUT_MS,
    );
  },
  importDevices: (options: ImportOptions) => {
    const form = new FormData();
    form.append("file", options.file);
    form.append("key_field", options.keyField);
    form.append("update_existing", String(options.updateExisting));
    if (options.mapping)
      form.append("mapping", JSON.stringify(options.mapping));
    return upload<ImportResult>(
      options.commit ? "/import/devices" : "/import/devices/preview",
      form,
      IMPORT_TIMEOUT_MS,
    );
  },

  /** 取り込みを裏で走らせる。番号だけ返るので、様子は job() で聞く。 */
  importDevicesInBackground: (options: ImportOptions) => {
    const form = new FormData();
    form.append("file", options.file);
    form.append("key_field", options.keyField);
    form.append("update_existing", String(options.updateExisting));
    if (options.mapping)
      form.append("mapping", JSON.stringify(options.mapping));
    return upload<Job>("/import/devices/async", form, IMPORT_TIMEOUT_MS);
  },
  importLinksInBackground: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return upload<Job>("/import/links/async", form, IMPORT_TIMEOUT_MS);
  },

  /** エラー行だけを抽出したCSVを取得する（Blob で受けてブラウザに保存させる）。 */
  importErrorsCsv: async (
    options: ImportOptions & { encoding?: "utf-8" | "shift_jis" },
  ) => {
    const form = new FormData();
    form.append("file", options.file);
    form.append("key_field", options.keyField);
    form.append("update_existing", String(options.updateExisting));
    form.append("encoding", options.encoding ?? "utf-8");
    if (options.mapping)
      form.append("mapping", JSON.stringify(options.mapping));

    const res = await send(
      "/import/devices/errors.csv",
      { method: "POST", body: form },
      IMPORT_TIMEOUT_MS,
    );
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
    request<ScenarioDetail>("/scenarios", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateScenario: (id: number, body: Partial<Scenario>) =>
    request<ScenarioDetail>(`/scenarios/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteScenario: (id: number) =>
    request<void>(`/scenarios/${id}`, { method: "DELETE" }),
  generateScenarioItems: (id: number, withinDays: number) =>
    request<ScenarioDetail>(
      `/scenarios/${id}/generate?within_days=${withinDays}`,
      {
        method: "POST",
      },
    ),
  scenarioSummary: (id: number) =>
    request<ScenarioSummary>(`/scenarios/${id}/summary`),
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
  uploadConfigs: (
    files: File[],
    options: {
      deviceId?: number;
      applyInventory?: boolean;
      createMissing?: boolean;
    } = {},
  ) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    if (options.deviceId !== undefined)
      form.append("device_id", String(options.deviceId));
    form.append("apply_inventory", String(options.applyInventory ?? false));
    form.append("create_missing", String(options.createMissing ?? false));
    return upload<IngestResult[]>("/configs/upload", form);
  },
  previewConfig: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return upload<IngestResult>("/configs/preview", form);
  },
  listConfigs: (deviceId?: number) =>
    request<ConfigSnapshot[]>(`/configs${query({ device_id: deviceId })}`),
  /** 1世代ぶんの中身。既定ではパスワードなどを伏せて返る。 */
  getConfig: (id: number, reveal = false) =>
    request<ConfigSnapshot & { content: string; masked_note: string }>(
      `/configs/${id}${reveal ? "?reveal=true" : ""}`,
    ),
  diffConfig: (id: number, against?: number) =>
    request<ConfigDiff>(`/configs/${id}/diff${query({ against })}`),
  deleteConfig: (id: number) =>
    request<void>(`/configs/${id}`, { method: "DELETE" }),
  listInterfaces: (deviceId: number) =>
    request<NetworkInterface[]>(`/configs/devices/${deviceId}/interfaces`),

  // --- トポロジー ---
  getTopology: (
    layer: string,
    includeRetired = false,
    orientation = "vertical",
    // 拠点が多い環境では全体図は使わない。絞った分＋対向だけを取る
    filter: { site_id?: number; q?: string; scope?: string } = {},
  ) =>
    request<TopologyGraph>(
      `/topology${query({
        layer,
        include_retired: String(includeRetired),
        orientation,
        ...filter,
      })}`,
    ),
  getTopologyStats: (layer: string) =>
    request<TopologyStats>(`/topology/stats${query({ layer })}`),
  /** 論理構成の材料。相手が特定できなかったトンネルも件数で返す。 */
  getTopologyTunnels: () =>
    request<{
      tunnel_count: number;
      device_count: number;
      unresolved: {
        device: string;
        name: string;
        destination: string | null;
        kind: string | null;
      }[];
    }>("/topology/tunnels"),
  savePositions: (
    layer: string,
    positions: { device_id: number; x: number; y: number }[],
    siteId?: number,
  ) =>
    request<{ saved: number }>("/topology/positions", {
      method: "PUT",
      body: JSON.stringify({ layer, site_id: siteId ?? null, positions }),
    }),
  createTopologyGroup: (body: {
    layer: string;
    name: string;
    device_ids: number[];
    plane?: string;
    color?: string;
    parent_id?: number | null;
  }) =>
    request<TopologyGroup>("/topology/groups", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateTopologyGroup: (
    id: number,
    body: {
      name?: string;
      plane?: string;
      color?: string;
      device_ids?: number[];
      parent_id?: number | null;
      clear_parent?: boolean;
    },
  ) =>
    request<TopologyGroup>(`/topology/groups/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteTopologyGroup: (id: number) =>
    request<void>(`/topology/groups/${id}`, { method: "DELETE" }),
  groupsFromSites: (layer: string, deviceIds: number[]) =>
    request<TopologyGroup[]>(
      `/topology/groups/from-sites${query({ layer, device_ids: deviceIds.join(",") })}`,
      { method: "POST" },
    ),
  groupsFromRacks: (layer: string, deviceIds: number[]) =>
    request<TopologyGroup[]>(
      `/topology/groups/from-racks${query({ layer, device_ids: deviceIds.join(",") })}`,
      { method: "POST" },
    ),
  groupsFromPlanes: (layer: string, deviceIds: number[]) =>
    request<TopologyGroup[]>(
      `/topology/groups/from-planes${query({ layer, device_ids: deviceIds.join(",") })}`,
      { method: "POST" },
    ),
  /** 配置を自動レイアウトに戻す。拠点や検索で絞っていればその範囲だけ。 */
  resetPositions: (
    layer: string,
    scope: { site_id?: number; q?: string } = {},
  ) =>
    request<{ reset: number }>(
      `/topology/positions${query({ layer, ...scope })}`,
      {
        method: "DELETE",
      },
    ),
  /** いま画面にある配置を起点に、マス目・揃いだけ整えて保存する。 */
  arrangePositions: (
    layer: string,
    scope: {
      site_id?: number;
      q?: string;
      scope?: string;
      orientation?: string;
    } = {},
    positions: { device_id: number; x: number; y: number }[] = [],
  ) =>
    request<{ arranged: number }>(
      `/topology/positions/arrange${query({ layer, ...scope })}`,
      {
        method: "POST",
        body: JSON.stringify({ positions }),
      },
    ),
  rebuildTopology: () =>
    request<RebuildResult>("/topology/rebuild", { method: "POST" }),
  listTopologyVlans: () => request<TopologyVlan[]>("/topology/vlans"),
  listTopologyVrfs: () => request<TopologyVrf[]>("/topology/vrfs"),

  // --- VLAN 台帳（人が用途を書く側。Config の取り込みでは上書きされない）---
  listVlanCatalog: () => request<VlanCatalogEntry[]>("/topology/vlans/catalog"),
  saveVlanCatalog: (vlanId: number, body: VlanCatalogWrite) =>
    request<VlanCatalogEntry>(`/topology/vlans/catalog/${vlanId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteVlanCatalog: (vlanId: number) =>
    request<void>(`/topology/vlans/catalog/${vlanId}`, { method: "DELETE" }),
  adoptObservedVlans: (overwrite = false) =>
    request<{ created: number; updated: number }>(
      `/topology/vlans/catalog/adopt${query({ overwrite: String(overwrite) })}`,
      { method: "POST" },
    ),

  // 構成図を SVG で書き出す（設計書や報告書に貼るため）
  topologyExportUrl: (
    layer: string,
    options: {
      title?: string;
      vlan?: number;
      vrf?: string;
      site_id?: number;
      style?: string;
      /** 自動配置の向き。vertical / horizontal */
      orientation?: string;
      /** ポート名の出し方。full / short / number */
      port_style?: string;
      /** ポート名を線に沿わせて回転する */
      along_line?: string;
    } = {},
  ) => `${BASE}/topology/export.svg${query({ layer, ...options })}`,
  /** 結線表。図が要らない場面（作業指示・検収）は表の方が使いやすい */
  linkTableUrl: (
    layer: string,
    format: "xlsx" | "csv",
    options: { site_id?: number; encoding?: string } = {},
  ) => `${BASE}/topology/export.${format}${query({ layer, ...options })}`,
  portMapUrl: (options: { site_id?: number } = {}) =>
    `${BASE}/topology/ports.xlsx${query({ ...options })}`,
  // 図・結線表・ポート一覧を1つにまとめた Excel。配るのはこれ1つで済む
  workbookUrl: (
    layer: string,
    options: { site_id?: number; scope?: string } = {},
  ) => `${BASE}/topology/workbook.xlsx${query({ layer, ...options })}`,
  designConfig: (deviceId: number) =>
    request<{
      device_id: number;
      hostname: string;
      body: string;
      link_count: number;
      vlan_count: number;
      links: {
        local_interface: string;
        peer_hostname: string;
        peer_interface: string;
        infrastructure: boolean;
      }[];
    }>(`/devices/${deviceId}/design-config`),

  /** 線の両端で、設定がそろっているか。 */
  linkCheck: () => request<LinkReport>("/topology/link-check"),

  /** 経路の情報から見つけた線の候補。LLDP が無い現場でも図を立ち上げる。 */
  linkCandidates: () => request<LinkCandidate[]>("/topology/link-candidates"),
  applyLinkCandidates: (pairs?: [number, number][]) =>
    request<{ created: number }>("/topology/link-candidates/apply", {
      method: "POST",
      body: JSON.stringify({ pairs: pairs ?? null }),
    }),

  /** 画面に出てくる言葉の説明。用語を知らないまま使えるようにするため。 */
  glossary: (q?: string) =>
    request<GlossaryTerm[]>(
      `/glossary${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),

  /** 似た機器の Config を重ねて、テンプレートの下書きを作る。 */
  templateFromConfig: (body: { hostname_prefix?: string; vendor?: string }) =>
    request<ConfigTemplateDraft>("/config-templates/from-config", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  saveTemplateFromConfig: (body: {
    hostname_prefix?: string;
    vendor?: string;
    name?: string;
  }) =>
    request<ConfigTemplate>("/config-templates/from-config/save", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Config をその場で見る。台帳には何も書かない。 */
  checkConfigText: (text: string, filename = "config.txt") =>
    request<ConfigCheck>("/config-check", {
      method: "POST",
      body: JSON.stringify({ text, filename }),
    }),
  checkConfigFiles: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return upload<ConfigCheck[]>("/config-check/upload", form);
  },

  /** 選んだ範囲だけを直してもらう。範囲の外は触らない。 */
  editSelection: (selection: string, instruction: string, description = "") =>
    request<ConfigEdit>("/config-check/edit", {
      method: "POST",
      // 引数名を request にすると、下の request() 関数が隠れる
      body: JSON.stringify({ selection, request: instruction, description }),
    }),

  /** 出した結果について聞き返す。 */
  askBack: (question: string, context = "") =>
    request<AskBackAnswer>("/config-check/ask", {
      method: "POST",
      body: JSON.stringify({ question, context }),
    }),

  /** 取り込んだ Config から覚えた、その現場の流儀。 */
  conventions: () => request<HouseStyle>("/conventions"),

  /** 何にどこまで対応しているか。できないことも返る。 */
  capabilities: () => request<CapabilityIndex>("/capabilities"),

  /** 条件から構成を起こす。案を見るだけで、台帳は触らない。 */
  blueprintConditions: () =>
    request<BlueprintCondition[]>("/blueprint/conditions"),
  drawBlueprint: (body: {
    a_name: string;
    b_name: string;
    conditions: string[];
    base?: string | null;
    vendor?: string;
  }) =>
    request<Blueprint>("/blueprint", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** 起こした構成を、テスト拠点として台帳に置く。図で見られるようになる。 */
  applyBlueprint: (body: {
    a_name: string;
    b_name: string;
    conditions: string[];
    base?: string | null;
    vendor?: string;
  }) =>
    request<{
      site_id: number;
      site_name: string;
      device_count: number;
      link_count: number;
    }>("/blueprint/apply", { method: "POST", body: JSON.stringify(body) }),
  testSites: () => request<TestSite[]>("/blueprint/test-sites"),
  removeTestSite: (siteId: number) =>
    request<{ removed_devices: number }>(`/blueprint/test-sites/${siteId}`, {
      method: "DELETE",
    }),

  /** その機器の L3（経路・OSPF・BGP・冗長）と、気になるところ。 */
  deviceL3: (deviceId: number) => request<L3Report>(`/devices/${deviceId}/l3`),

  /** 書いた文章から、増設の設計を起こす。読み取った内容も返る。 */
  askAddition: (body: {
    text: string;
    upstream_device_id?: number | null;
    new_hostname?: string | null;
    purpose?: string | null;
  }) =>
    request<AskResult>("/ask/addition", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** その機器の下に1台足すときの設計。決めた理由と根拠つきで返る。 */
  planAddition: (
    deviceId: number,
    body: {
      hostname: string;
      vendor?: string;
      model?: string;
      purpose?: string;
      vlan_id?: number | null;
      subnet?: string | null;
      uplink_interface?: string | null;
    },
  ) =>
    request<AdditionPlan>(`/devices/${deviceId}/addition`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createLink: (body: {
    a_device_id: number;
    b_device_id: number;
    a_interface?: string;
    b_interface?: string;
    label?: string | null;
  }) =>
    request<TopologyEdge>("/topology/links", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateLink: (
    id: number,
    body: {
      a_interface?: string;
      b_interface?: string;
      label?: string;
      /** 図の上でどの辺から線を出すか。null に戻すと自動に任せる */
      a_side?: string | null;
      b_side?: string | null;
    },
  ) =>
    request<TopologyEdge>(`/topology/links/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteLink: (id: number) =>
    request<void>(`/topology/links/${id}`, { method: "DELETE" }),

  // --- リファレンス（型番のライフサイクル / 脆弱性）---
  listProducts: (params: { q?: string; stale_only?: string } = {}) =>
    request<ProductReference[]>(`/reference/products${query(params)}`),
  createProduct: (body: Partial<ProductReference>) =>
    request<ProductReference>("/reference/products", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateProduct: (id: number, body: Partial<ProductReference>) =>
    request<ProductReference>(`/reference/products/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProduct: (id: number) =>
    request<void>(`/reference/products/${id}`, { method: "DELETE" }),
  importProducts: (file: File, fetchedAt?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (fetchedAt) form.append("fetched_at", fetchedAt);
    return upload<ProductImportResult>("/reference/products/import", form);
  },
  productTemplateUrl: `${BASE}/reference/products/template.csv`,

  importVulnerabilities: (file: File, fetchedAt?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (fetchedAt) form.append("fetched_at", fetchedAt);
    return upload<VulnerabilityImportResult>(
      "/reference/vulnerabilities/import",
      form,
    );
  },
  listVulnerabilities: (params: { q?: string; affecting_only?: string } = {}) =>
    request<Vulnerability[]>(`/reference/vulnerabilities${query(params)}`),
  deleteVulnerability: (id: number) =>
    request<void>(`/reference/vulnerabilities/${id}`, { method: "DELETE" }),
  vulnerabilityMatches: (deviceId?: number) =>
    request<VulnerabilitySummary>(
      `/reference/vulnerabilities/matches/summary${query({ device_id: deviceId })}`,
    ),
  referenceFreshness: () => request<ReferenceFreshness>("/reference/freshness"),

  // --- ベンダードキュメント ---
  listDocuments: (
    params: { q?: string; vendor?: string; doc_type?: string } = {},
  ) => request<VendorDocument[]>(`/documents${query(params)}`),
  createDocument: (fields: Record<string, string>, file?: File | null) => {
    const form = new FormData();
    for (const [key, value] of Object.entries(fields)) {
      if (value) form.append(key, value);
    }
    if (file) form.append("file", file);
    return upload<VendorDocument>("/documents", form);
  },
  updateDocument: (id: number, body: Partial<VendorDocument>) =>
    request<VendorDocument>(`/documents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  supersedeDocument: (id: number, successorId: number) =>
    request<VendorDocument>(`/documents/${id}/supersede/${successorId}`, {
      method: "POST",
    }),
  deleteDocument: (id: number) =>
    request<void>(`/documents/${id}`, { method: "DELETE" }),
  documentDownloadUrl: (id: number) => `${BASE}/documents/${id}/download`,
  documentsForDevice: (deviceId: number) =>
    request<VendorDocumentMatch[]>(`/devices/${deviceId}/documents`),
  /** 取り込んだ資料の中を探す。閉域なので外の検索には出さない */
  searchDocuments: (params: { q: string; vendor?: string; model?: string }) =>
    request<DocumentHit[]>(`/documents/search${query(params)}`),
  /** その機器に合う資料の中だけを探す */
  searchDocumentsForDevice: (deviceId: number, q: string) =>
    request<DocumentHit[]>(
      `/devices/${deviceId}/document-search${query({ q })}`,
    ),
  reindexDocument: (id: number) =>
    request<VendorDocument>(`/documents/${id}/index`, { method: "POST" }),

  // --- Config テンプレート ---
  listTemplates: (
    params: { q?: string; vendor?: string; category?: string } = {},
  ) => request<ConfigTemplate[]>(`/config-templates${query(params)}`),
  createTemplate: (body: Partial<ConfigTemplate>) =>
    request<ConfigTemplate>("/config-templates", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateTemplate: (id: number, body: Partial<ConfigTemplate>) =>
    request<ConfigTemplate>(`/config-templates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteTemplate: (id: number) =>
    request<void>(`/config-templates/${id}`, { method: "DELETE" }),
  templatesForDevice: (deviceId: number) =>
    request<ConfigTemplateMatch[]>(`/devices/${deviceId}/config-templates`),
  renderTemplate: (
    id: number,
    body: { device_id?: number; variables: Record<string, string> },
  ) =>
    request<RenderResult>(`/config-templates/${id}/render`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- Config 計画 ---
  listPlans: (deviceId?: number) =>
    request<ConfigPlan[]>(`/config-plans${query({ device_id: deviceId })}`),
  createPlan: (body: Record<string, unknown>) =>
    request<ConfigPlan>("/config-plans", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updatePlan: (id: number, body: Partial<ConfigPlan>) =>
    request<ConfigPlan>(`/config-plans/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deletePlan: (id: number) =>
    request<void>(`/config-plans/${id}`, { method: "DELETE" }),
  planOutputs: (id: number) =>
    request<PlanOutputs>(`/config-plans/${id}/outputs`),
  draftConfig: (deviceId: number, body: { request: string; base?: string }) =>
    request<ConfigDraft>(`/devices/${deviceId}/config-draft`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** 画面から決める設定 */
  appSettings: () => request<AppSettings>("/settings"),
  updateAppSettings: (body: Partial<AppSettings>) =>
    request<AppSettings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  llmStatus: () => request<LlmStatus>("/llm/status"),
  /** モデルを使うかどうかの切り替え。次の起動でも残る */
  setLlmEnabled: (enabled: boolean) =>
    request<LlmStatus>("/llm/enabled", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  /** 下書きを裏で作る。受け取った番号で様子を聞く */
  draftConfigInBackground: (
    deviceId: number,
    body: { request: string; base?: string },
  ) =>
    request<Job>(`/devices/${deviceId}/config-draft/async`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  job: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  /** 裏で走らせた仕事の一覧。新しい順 */
  jobs: (limit = 20) => request<Job[]>(`/jobs?limit=${limit}`),
  cancelJob: (jobId: string) =>
    request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  /** 手元にモデルを置けないとき、承認済みの AI へ貼るための依頼文 */
  draftPrompt: (deviceId: number, body: { request: string; base?: string }) =>
    request<ConfigDraft>(`/devices/${deviceId}/config-draft/prompt`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** よそで書いた設定を、資料と突き合わせて検める */
  reviewDraft: (deviceId: number, body: { request: string; body: string }) =>
    request<ConfigDraft>(`/devices/${deviceId}/config-draft/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  planDownloadUrl: (
    id: number,
    kind: "current" | "future" | "diff" | "rollback",
  ) => `${BASE}/config-plans/${id}/download/${kind}`,

  // --- 対処状況（対処漏れ防止） ---
  syncExposures: () =>
    request<ExposureSyncResult>("/exposures/sync", { method: "POST" }),
  listExposures: (
    params: { status_filter?: string; unhandled_only?: boolean } = {},
  ) =>
    request<ExposureRow[]>(
      `/exposures${query({
        status_filter: params.status_filter,
        unhandled_only: params.unhandled_only ? "true" : undefined,
      })}`,
    ),
  exposureSummary: () => request<ExposureSummary>("/exposures/summary"),
  decideExposure: (id: number, body: Record<string, unknown>) =>
    request<ExposureRow>(`/exposures/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  verifyExposureConfig: (id: number) =>
    request<ExposureRow>(`/exposures/${id}/verify-config`, { method: "POST" }),
  fixConfig: (deviceId: number, remediationId: number) =>
    request<FixConfig>(
      `/devices/${deviceId}/remediations/${remediationId}/config`,
    ),
  fixConfigDownloadUrl: (
    deviceId: number,
    remediationId: number,
    kind: "current" | "fixed" | "diff" | "rollback",
  ) =>
    `${BASE}/devices/${deviceId}/remediations/${remediationId}/config/download/${kind}`,

  // --- 脆弱性への対処 ---
  deviceRemediation: (deviceId: number) =>
    request<DeviceRemediation>(`/devices/${deviceId}/remediation`),
  listRemediations: (cveId?: string) =>
    request<Remediation[]>(`/remediations${query({ cve_id: cveId })}`),
  createRemediation: (body: Record<string, unknown>) =>
    request<Remediation>("/remediations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteRemediation: (id: number) =>
    request<void>(`/remediations/${id}`, { method: "DELETE" }),

  // --- 更改支援 ---
  replacementPlan: (years = 5) =>
    request<ReplacementPlan>(`/replacement/plan${query({ years })}`),
  replacementImpact: (deviceId: number) =>
    request<ImpactReport>(`/replacement/impact/${deviceId}`),

  meta: () => request<AppMeta>("/meta"),
  // プレビューの初回は表の作成とデモ投入が走る。8秒だと「起動できませんでした」になる
  schemaStatus: () => request<SchemaStatus>("/schema", undefined, 25_000),
  applySchema: () => request<SchemaStatus>("/schema/apply", { method: "POST" }),
  confirmSchema: () =>
    request<SchemaStatus>("/schema/confirm", { method: "POST" }),

  listSites: () => request<Site[]>("/sites"),
  createSite: (body: Partial<Site>) =>
    request<Site>("/sites", { method: "POST", body: JSON.stringify(body) }),
  updateSite: (id: number, body: Partial<Site>) =>
    request<Site>(`/sites/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteSite: (id: number) =>
    request<void>(`/sites/${id}`, { method: "DELETE" }),
};
