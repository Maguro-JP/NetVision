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
  /** "device" = 機器個別の入力 / "product" = 型番の製品マスター / null = 未登録 */
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
  /** 人が決める管理ID。社内台帳の番号など */
  code: string | null;
  /** 人が付けた機器の名称。空ならホスト名で呼ぶ */
  label: string | null;
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
  /** 埋まっていない項目の名前。Config から起こした機器は空の項目が残る */
  missing_fields: string[];
  /** 台帳に載った日時。取り込んだばかりの機器を上に出すのに使う */
  created_at: string | null;
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
  /** 人が決める拠点ID */
  code: string | null;
  name: string;
  address: string | null;
  /** いまどういう状態か。閉鎖しても機器は残る */
  status: SiteStatus;
  /** 開いた日・閉じた日。棚卸しのときに要る */
  opened_on: string | null;
  closed_on: string | null;
  notes: string | null;
  device_count: number;
}

export type SiteStatus = "active" | "planned" | "suspended" | "closed";

/** 資料の中を探した結果。1件が1つの章 */
export interface DocumentHit {
  document_id: number;
  title: string;
  vendor: string;
  model: string | null;
  heading: string | null;
  body: string;
  /** その章に出てくるコマンド。書き方をそのまま拾ったもの */
  commands: string[];
}

export interface ImportField {
  name: string;
  label: string;
  required: boolean;
  aliases: string[];
  /** 決まった書き方しか受け付けない項目の候補（種別・状態）。空なら自由入力 */
  choices: string[];
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
  /** 行結果の実数。rows は画面が固まらないよう一部だけ返す */
  rows_total: number;
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
  native_vlan: number | null;
  /** 許可 VLAN。`10,20,30-40` か `all` の形で入る */
  trunk_vlans: string | null;
  vrf: string | null;
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
  site_id: number | null;
  site_name: string | null;
  mgmt_ip: string | null;
  severity: Severity;
  interface_count: number;
  x: number;
  y: number;
  placed: boolean;
  vlans: number[];
  vrfs: string[];
  /** この機器で図に出てくるポートの種類（GigabitEthernet など） */
  port_kinds: string[];
  /** 絞り込みの外にいて、対向として出しているだけの機器 */
  outside: boolean;
  /** 同じ拠点にいて、代表1台にまとめた分の台数。境界の冗長対は折りたたまない */
  hidden_peers: number;
  /** 対向の向き。up=上位 / down=配下 / side=横 */
  peer_kind?: "up" | "down" | "side" | null;
  /** 現場で機器を特定する手がかり。図の箱に出す */
  serial_number: string | null;
  /** 人が付けた機器の名称。空ならホスト名で呼ぶ */
  label: string | null;
  /** 人が決めた管理ID */
  code: string | null;
  /** キャリア網の段。backbone / metro / access */
  plane?: "backbone" | "metro" | "access";
}

export interface TopologyGroup {
  id: number;
  name: string;
  plane: "backbone" | "metro" | "access" | "custom";
  color: string;
  device_ids: number[];
  /** 外側の枠（フロアや部屋）。無ければ null */
  parent_id: number | null;
}

export interface TopologyVlan {
  vlan_id: number;
  /** 表示に使う名前。台帳にあればそちら、無ければ実機の名前 */
  name: string | null;
  purpose: string | null;
  owner: string | null;
  subnet: string | null;
  /** 台帳に登録済みか。未登録は素性の分からないセグメント */
  in_catalog: boolean;
  /** 実機の Config に書かれている名前 */
  observed_names: string[];
  device_ids: number[];
  device_names: string[];
  /** VLAN を定義している機器。空ならトランクを通っているだけ */
  defined_device_ids: number[];
  access_port_count: number;
  trunk_port_count: number;
  native_port_count: number;
}

export interface TopologyVrf {
  name: string;
  rd: string | null;
  description: string | null;
  device_ids: number[];
  device_names: string[];
  interface_count: number;
  /** 同じ VRF 名なのに機器ごとに RD が違う */
  rd_mismatch: boolean;
}

export interface VlanCatalogEntry {
  id: number;
  vlan_id: number;
  name: string | null;
  purpose: string | null;
  owner: string | null;
  subnet: string | null;
  notes: string | null;
}

export type VlanCatalogWrite = Partial<
  Omit<VlanCatalogEntry, "id" | "vlan_id">
>;

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
  /** この線が実際に運んでいる VLAN（両端の設定を突き合わせたもの） */
  vlans: number[];
  /** 片側にしか無い VLAN。トランクの許可漏れ */
  vlan_mismatch: number[];
  native_vlan: number | null;
  native_mismatch: boolean;
  vrf: string | null;
  /** 人が指定した引き出し位置。null なら位置関係から自動で決める */
  source_side: LinkSide | null;
  target_side: LinkSide | null;
}

export type LinkSide = "top" | "bottom" | "left" | "right";

export interface TopologyGraph {
  layer: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  unplaced_count: number;
  groups?: TopologyGroup[];
}

export interface TopologyStats {
  device_count: number;
  link_count: number;
  discovered_links: number;
  manual_links: number;
  confirmed_links: number;
  /** 繋がっていない機器の名前。多いときは頭から少しだけ */
  isolated_devices: string[];
  /** 繋がっていない機器の総数 */
  isolated_count: number;
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

export type DocumentType =
  | "command_reference"
  | "config_guide"
  | "release_notes"
  | "install_guide"
  | "hardware_guide"
  | "security_advisory"
  | "datasheet"
  | "other";

export interface VendorDocument {
  id: number;
  vendor: string;
  model: string | null;
  os_name: string | null;
  doc_type: DocumentType;
  title: string;
  language: string | null;
  applies_from: string | null;
  applies_to: string | null;
  version_label: string | null;
  edition: string | null;
  published_at: string | null;
  source_url: string | null;
  fetched_at: string | null;
  notes: string | null;
  filename: string | null;
  content_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  has_file: boolean;
  superseded_by_id: number | null;
  superseded_by_title: string | null;
  device_count: number;
  /** 中身を章に切った日時。読めなかった資料は index_note に理由が入る */
  indexed_at: string | null;
  index_note: string | null;
  section_count: number;
}

export interface VendorDocumentMatch extends VendorDocument {
  reason: string;
}

export type TemplateCategory =
  | "interface"
  | "vlan"
  | "routing"
  | "acl"
  | "qos"
  | "management"
  | "security"
  | "other";

export interface TemplateVariable {
  name: string;
  label?: string;
  required?: boolean;
  default?: string;
  help?: string;
}

export interface ConfigTemplate {
  id: number;
  name: string;
  description: string | null;
  category: TemplateCategory;
  vendor: string;
  model: string | null;
  os_name: string | null;
  applies_from: string | null;
  applies_to: string | null;
  version_label: string | null;
  body: string;
  variables: TemplateVariable[];
  source_document_id: number | null;
  source_document_title: string | null;
  superseded_by_id: number | null;
  detected_variables: TemplateVariable[];
}

export interface ConfigTemplateMatch extends ConfigTemplate {
  reason: string;
}

export interface RenderResult {
  rendered: string;
  device_hostname: string | null;
  used_variables: Record<string, string>;
  warnings: string[];
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

// --- Config 計画（現行フル / 将来フル / 差分） ---

export type PlanStatus =
  | "draft"
  | "review"
  | "approved"
  | "applied"
  | "archived";

export interface ConfigPlan {
  id: number;
  device_id: number;
  device_hostname: string | null;
  name: string;
  description: string | null;
  status: PlanStatus;
  body: string;
  vendor: string | null;
  scenario_id: number | null;
  base_snapshot_id: number | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface DiffCommand {
  text: string;
  depth: number;
  kind: "add" | "remove" | "context";
  rendered: string;
}

export interface PlanDiff {
  verified: boolean;
  commands: DiffCommand[];
  script: string;
  rollback_script: string;
  mismatches: string[];
  warnings: string[];
  risks: string[];
  added: number;
  removed: number;
  changed: number;
}

export interface PlanOutputs {
  plan_id: number;
  device_hostname: string | null;
  vendor: string;
  base_snapshot_id: number | null;
  base_is_stale: boolean;
  notes: string[];
  current_full: string;
  future_full: string;
  diff: PlanDiff;
}

/** 資料を根拠にした Config の下書き。 */
export interface ConfigDraftLine {
  text: string;
  /** 資料に同じ書き方が出てくるか */
  grounded: boolean;
  risks: string[];
}

export interface ConfigDraftSource {
  document_id: number;
  title: string;
  heading: string | null;
  commands: string[];
}

export interface ConfigDraft {
  device_id: number;
  vendor: string;
  body: string;
  lines: ConfigDraftLine[];
  sources: ConfigDraftSource[];
  risks: string[];
  notes: string[];
  /** 下書きを起こしたモデル。資料から拾っただけなら null */
  model: string | null;
}

export interface LlmModel {
  role: string;
  model: string;
  endpoint: string;
  local: boolean;
  usable: boolean;
  blocked_external: boolean;
}

export interface LlmStatus {
  configured: boolean;
  models: LlmModel[];
}

// --- 更改支援 ---

export interface ReplacementTarget {
  device_id: number;
  hostname: string;
  vendor: string | null;
  model: string | null;
  site_name: string | null;
  reason: string;
  due_on: string | null;
  severity: string;
  estimated_cost: number | null;
  impact_count: number;
}

export interface FiscalYearPlan {
  fiscal_year: number;
  label: string;
  device_count: number;
  estimated_cost: number;
  targets: ReplacementTarget[];
}

export interface ReplacementPlan {
  evaluated_on: string;
  years: FiscalYearPlan[];
  unscheduled: ReplacementTarget[];
  total_cost: number;
  total_devices: number;
}

export interface ImpactReport {
  device_id: number;
  hostname: string;
  directly_connected: string[];
  isolated: string[];
  note: string;
}

// --- 脆弱性への対処 ---

export type RemediationKind =
  | "upgrade"
  | "workaround"
  | "mitigation"
  | "accepted"
  | "not_applicable";

export interface RemediationOption {
  id: number;
  kind: string;
  summary: string;
  fixed_version: string | null;
  procedure: string | null;
  source_document_id: number | null;
  source_document_title: string | null;
  config_template_id: number | null;
  config_template_name: string | null;
  verified_by: string | null;
  verified_on: string | null;
  matched_on: string;
}

export interface RemediationSuggestion {
  id: number;
  title?: string | null;
  name?: string | null;
  reason: string;
}

export interface RemediationFinding {
  cve_id: string;
  title: string | null;
  cvss_score: number | null;
  cvss_severity: string | null;
  known_exploited: boolean;
  matched_on: string;
  fixed_version: string | null;
  upgrade_available: boolean;
  has_remediation: boolean;
  remediations: RemediationOption[];
  suggested_documents: RemediationSuggestion[];
  suggested_templates: RemediationSuggestion[];
}

export interface DeviceRemediation {
  device_id: number;
  hostname: string;
  os_version: string | null;
  findings: RemediationFinding[];
  unresolved_count: number;
  note: string;
}

export interface Remediation extends RemediationOption {
  vulnerability_id: number;
  cve_id: string | null;
  vendor: string | null;
  model: string | null;
  os_name: string | null;
  applies_from: string | null;
  applies_to: string | null;
  notes: string | null;
}

// --- 対処状況（対処漏れ防止） ---

export interface ExposureRow {
  id: number;
  device_id: number;
  hostname: string;
  os_version: string | null;
  cve_id: string;
  title: string | null;
  cvss_score: number | null;
  known_exploited: boolean;
  status: string;
  remediation_id: number | null;
  remediation_summary: string | null;
  decided_by: string | null;
  decided_on: string | null;
  first_seen_on: string | null;
  reopened_on: string | null;
  reopen_reason: string | null;
  config_verified: boolean | null;
  config_verified_on: string | null;
  due_on: string | null;
  overdue: boolean;
  note: string | null;
}

export interface ExposureSummary {
  total: number;
  unhandled: number;
  known_exploited_unhandled: number;
  overdue: number;
  mitigated_but_still_affected: number;
  reopened: number;
  unverified: number;
  by_status: Record<string, number>;
}

export interface ExposureSyncResult {
  created: number;
  reopened: number;
  resolved: number;
  unchanged: number;
  messages: string[];
}

export interface FixConfig {
  device_id: number;
  hostname: string;
  cve_id: string;
  remediation_id: number;
  remediation_summary: string;
  vendor: string;
  source: string;
  current_full: string;
  fixed_full: string;
  diff: PlanDiff;
  already_applied: boolean;
  notes: string[];
}

export interface AppMeta {
  demo_mode: boolean;
  warning_days: number;
  critical_days: number;
  version?: string;
  schema_revision?: string | null;
  schema_phase?: SchemaPhase;
}

export type SchemaPhase = "ready" | "needs_apply" | "needs_confirm";

export interface SchemaStep {
  revision: string;
  summary: string;
}

export interface SchemaBackup {
  id: string;
  path: string;
  created_at: string;
  from_revision: string | null;
  to_revision: string | null;
  bytes: number;
  documents_bytes: number;
  kind: string;
  confirmed_at: string | null;
}

export interface SchemaStatus {
  phase: SchemaPhase;
  current_revision: string | null;
  head_revision: string;
  pending: SchemaStep[];
  counts: { devices: number; sites: number } | null;
  unconfirmed: SchemaBackup[];
}

/** 結線表の取り込み結果。 */
export interface LinkImportResult {
  total_rows: number;
  /** 両端の機器が揃っていて、結線として作れた行 */
  linked: number;
  /** 既に同じ結線があった行 */
  already: number;
  /** 機器がまだ無いので預かった行 */
  pending: number;
  issues: { row_number: number; message: string }[];
  unmapped_columns: string[];
  committed: boolean;
}

/** 資料から起こしたテンプレートの下書き。 */
export interface TemplateDraft {
  name: string;
  category: string;
  vendor: string;
  model: string | null;
  body: string;
  variables: { name: string; label: string; default: string; help: string }[];
  heading: string | null;
  notes: string[];
}

/** 拠点台帳の取り込み結果。 */
export interface SiteImportResult {
  total_rows: number;
  created: number;
  updated: number;
  /** 表と中身が同じで、何も変えなかった行 */
  unchanged: number;
  issues: { row_number: number; message: string }[];
  unmapped_columns: string[];
  committed: boolean;
}

/** 眺めるための、拠点単位にまとめた姿。 */
export interface SpaceView {
  sites: {
    site_id: number | null;
    name: string;
    address: string | null;
    device_count: number;
    severity: string;
  }[];
  edges: { a: number | null; b: number | null; count: number }[];
  device_total: number;
  link_total: number;
}
