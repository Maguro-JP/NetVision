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
