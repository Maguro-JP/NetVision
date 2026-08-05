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
