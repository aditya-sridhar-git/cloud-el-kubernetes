import type { DeployStatus } from "../types";

const TONE: Record<DeployStatus, string> = {
  QUEUED: "neutral",
  CLONING: "progress",
  BUILDING: "progress",
  APPLYING: "progress",
  ROLLING_OUT: "progress",
  SUCCEEDED: "good",
  FAILED: "bad",
  ROLLED_BACK: "warn",
};

export default function StatusBadge({ status }: { status: DeployStatus | null | undefined }) {
  if (!status) return <span className="badge neutral">NOT DEPLOYED</span>;
  return <span className={`badge ${TONE[status]}`}>{status.replace("_", " ")}</span>;
}
