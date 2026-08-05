import type { Severity } from "../api/types";
import { SEVERITY_LABEL } from "../lib/format";

export default function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge ${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}
