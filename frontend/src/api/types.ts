export type Severity = "expired" | "critical" | "warning" | "ok" | "unknown";

export type DeviceType =
  | "router"
  | "switch"
  | "firewall"
  | "load_balancer"
  | "wireless_ap"
  | "wireless_controller"
  | "server"
  | "other";

export type DeviceStatus = "active" | "standby" | "maintenance" | "retired";

export interface LifecycleStatus {
  effective_eos_date: string | null;
  effective_eol_date: string | null;
  /** "device" = 機器個別の入力 / "product" = 型番の製品マスタ / null = 未登録 */
  eol_source: "device" | "product" | null;
  maintenance_end: string | null;
  days_to_maintenance_end: number | null;
  maintenance_severity: Severity;
  days_to_eos: number | null;
  eos_severity: Severity;
  days_to_eol: number | null;
  eol_severity: Severity;
  overall_severity: Severity;
  age_years: number | null;
}

export interface Device {
  id: number;
  hostname: string;
  vendor: string;
  model: string;
  serial_number: string | null;
  device_type: DeviceType;
  status: DeviceStatus;
  os_name: string | null;
  os_version: string | null;
  firmware_version: string | null;
  site_id: number | null;
  site_name: string | null;
  rack: string | null;
  rack_unit: string | null;
  mgmt_ip: string | null;
  install_date: string | null;
  eos_date: string | null;
  eol_date: string | null;
  notes: string | null;
  lifecycle: LifecycleStatus;
}

export interface MaintenanceContract {
  id: number;
  device_id: number;
  contract_number: string | null;
  provider: string;
  service_level: string | null;
  start_date: string;
  end_date: string;
  annual_cost: number | null;
  contact: string | null;
  notes: string | null;
}

export interface DeviceDetail extends Device {
  contracts: MaintenanceContract[];
}

export interface Site {
  id: number;
  name: string;
  address: string | null;
  notes: string | null;
  device_count: number;
}

export interface ImportField {
  name: string;
  label: string;
  required: boolean;
  aliases: string[];
}

export type ImportRowAction = "create" | "update" | "skip" | "error";

export interface ImportRowIssue {
  column: string | null;
  message: string;
}

export interface ImportRowResult {
  row_number: number;
  key: string;
  action: ImportRowAction;
  issues: ImportRowIssue[];
  device_id: number | null;
  contract_added: boolean;
}

export interface ImportResult {
  committed: boolean;
  total_rows: number;
  created: number;
  updated: number;
  skipped: number;
  errors: number;
  contracts_added: number;
  mapping: Record<string, string>;
  unmapped_columns: string[];
  missing_required_fields: string[];
  column_samples: Record<string, string[]>;
  rows: ImportRowResult[];
}

export type ConfigVendor =
  | "cisco_ios"
  | "cisco_nxos"
  | "juniper"
  | "fortinet"
  | "unknown";

export interface FieldDiff {
  field: string;
  label: string;
  current: string | null;
  observed: string | null;
  differs: boolean;
}

export interface UnresolvedNeighbor {
  local_device: string;
  local_interface: string;
  remote_hostname: string;
  remote_platform: string | null;
}

export interface IngestResult {
  filename: string;
  vendor: ConfigVendor;
  vendor_label: string;
  hostname: string | null;
  device_id: number | null;
  device_hostname: string | null;
  matched: boolean;
  unchanged: boolean;
  snapshot_id: number | null;
  interface_count: number;
  neighbor_count: number;
  vlan_count: number;
  links_created: number;
  diffs: FieldDiff[];
  applied_fields: string[];
  unresolved_neighbors: UnresolvedNeighbor[];
  message: string | null;
}

export interface ConfigSnapshot {
  id: number;
  device_id: number | null;
  hostname: string | null;
  filename: string;
  sha256: string;
  vendor: string | null;
  parsed_os_name: string | null;
  parsed_os_version: string | null;
  parsed_model: string | null;
  parsed_serial: string | null;
  interface_count: number;
  neighbor_count: number;
  created_at: string;
}

export interface ConfigDiff {
  from_id: number;
  to_id: number;
  diff: string;
  changed: boolean;
}

export interface NetworkInterface {
  id: number;
  device_id: number;
  name: string;
  description: string | null;
  ip_address: string | null;
  vlan: number | null;
  mode: string | null;
  enabled: boolean;
}

export type LinkOrigin = "discovered" | "manual";

export interface TopologyNode {
  device_id: number;
  hostname: string;
  vendor: string;
  model: string;
  device_type: DeviceType;
  status: DeviceStatus;
  site_name: string | null;
  mgmt_ip: string | null;
  severity: Severity;
  interface_count: number;
  x: number;
  y: number;
  placed: boolean;
}

export interface TopologyEdge {
  id: number;
  source: number;
  target: number;
  source_interface: string;
  target_interface: string;
  origin: LinkOrigin;
  protocol: string | null;
  label: string | null;
  confirmed_both_ways: boolean;
}

export interface TopologyGraph {
  layer: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  unplaced_count: number;
}

export interface TopologyStats {
  device_count: number;
  link_count: number;
  discovered_links: number;
  manual_links: number;
  confirmed_links: number;
  isolated_devices: string[];
}

export interface RebuildResult {
  links_created: number;
  unresolved_neighbors: UnresolvedNeighbor[];
}

export type ReferenceSourceKind = "manual" | "csv" | "nvd" | "kev" | "vendor";

export interface ProductReference {
  id: number;
  vendor: string;
  model: string;
  product_name: string | null;
  eos_date: string | null;
  eol_date: string | null;
  end_of_sw_date: string | null;
  last_support_date: string | null;
  recommended_replacement: string | null;
  source: ReferenceSourceKind;
  source_url: string | null;
  fetched_at: string | null;
  notes: string | null;
  device_count: number;
  age_days: number | null;
  stale: boolean;
}

export interface ProductImportRow {
  row_number: number;
  vendor: string | null;
  model: string | null;
  action: string;
  message: string | null;
}

export interface ProductImportResult {
  total_rows: number;
  created: number;
  updated: number;
  unchanged: number;
  errors: number;
  mapping: Record<string, string>;
  unmapped_columns: string[];
  missing_required_fields: string[];
  rows: ProductImportRow[];
}

export interface Vulnerability {
  id: number;
  cve_id: string;
  title: string | null;
  description: string | null;
  cvss_score: number | null;
  cvss_severity: string | null;
  published: string | null;
  last_modified: string | null;
  known_exploited: boolean;
  remediation_due: string | null;
  source: ReferenceSourceKind;
  source_url: string | null;
  fetched_at: string | null;
  affected_device_count: number;
}

export interface VulnerabilityImportResult {
  format: string;
  total: number;
  created: number;
  updated: number;
  skipped: number;
  affects_created: number;
  messages: string[];
}

export interface DeviceVulnerability {
  device_id: number;
  hostname: string;
  cve_id: string;
  title: string | null;
  cvss_score: number | null;
  cvss_severity: string | null;
  known_exploited: boolean;
  fixed_version: string | null;
  matched_on: string;
  source: string;
  fetched_at: string | null;
}

export interface VulnerabilitySummary {
  total_vulnerabilities: number;
  devices_affected: number;
  known_exploited_findings: number;
  critical_findings: number;
  high_findings: number;
  devices_without_version: number;
  findings: DeviceVulnerability[];
}

export interface ReferenceFreshness {
  product_count: number;
  products_with_dates: number;
  products_stale: number;
  products_never_fetched: number;
  oldest_product_fetch: string | null;
  vulnerability_count: number;
  vulnerabilities_stale: number;
  oldest_vulnerability_fetch: string | null;
  stale_after_days: number;
  devices_total: number;
  devices_with_product_reference: number;
  devices_without_reference: string[];
}

export type InsightCategory = "cost" | "risk" | "data" | "consolidation";

export interface Insight {
  kind: string;
  category: InsightCategory;
  title: string;
  severity: Severity;
  detail: string;
  device_id: number | null;
  device_hostname: string | null;
  annual_saving: number | null;
  recommendation: string;
}

export interface InsightReport {
  total: number;
  annual_saving_total: number;
  by_category: Record<string, number>;
  insights: Insight[];
}

export type ScenarioStatus = "draft" | "review" | "approved" | "archived";
export type ScenarioAction = "replace" | "add" | "remove" | "keep";

export interface ScenarioItem {
  id: number;
  scenario_id: number;
  action: ScenarioAction;
  device_id: number | null;
  device_label: string | null;
  planned_vendor: string | null;
  planned_model: string | null;
  quantity: number;
  unit_cost: number | null;
  planned_annual_cost: number | null;
  planned_install_date: string | null;
  planned_eol_date: string | null;
  notes: string | null;
}

export interface Scenario {
  id: number;
  name: string;
  description: string | null;
  status: ScenarioStatus;
  target_date: string | null;
  item_count: number;
}

export interface ScenarioDetail extends Scenario {
  items: ScenarioItem[];
}

export interface ConfigurationMetrics {
  device_count: number;
  annual_maintenance_cost: number;
  expired_count: number;
  at_risk_count: number;
  unsupported_count: number;
  average_age_years: number | null;
}

export interface ScenarioSummary {
  scenario_id: number;
  name: string;
  status: string;
  evaluated_on: string;
  current: ConfigurationMetrics;
  projected: ConfigurationMetrics;
  investment: number;
  annual_cost_delta: number;
  item_counts: Record<string, number>;
  warnings: string[];
}

export type ChangeAction = "create" | "update" | "delete";
export type ChangeSource = "ui" | "import" | "seed" | "system";

export interface ChangeLog {
  id: number;
  entity_type: string;
  entity_id: number | null;
  entity_label: string;
  action: ChangeAction;
  field: string | null;
  field_label: string | null;
  old_value: string | null;
  new_value: string | null;
  source: ChangeSource;
  actor: string | null;
  batch_id: string | null;
  note: string | null;
  created_at: string;
}

export interface ChangeLogPage {
  total: number;
  items: ChangeLog[];
}

export type SeverityCounts = Record<Severity, number>;

export interface DashboardSummary {
  total_devices: number;
  active_devices: number;
  by_overall_severity: SeverityCounts;
  by_maintenance_severity: SeverityCounts;
  by_eol_severity: SeverityCounts;
  by_vendor: { vendor: string; count: number }[];
  devices_without_lifecycle_data: number;
  upcoming: Device[];
}
