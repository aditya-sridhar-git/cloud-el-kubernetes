export type DeployStatus =
  | "QUEUED"
  | "CLONING"
  | "BUILDING"
  | "APPLYING"
  | "ROLLING_OUT"
  | "SUCCEEDED"
  | "FAILED"
  | "ROLLED_BACK";

export interface Deployment {
  id: number;
  app_name: string;
  trigger: string;
  status: DeployStatus;
  commit_sha: string | null;
  image: string | null;
  spec_json: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface AppConfig {
  branch: string;
  port: number;
  health_path: string;
  cpu_request_m: number;
  cpu_limit_m: number;
  mem_request_mi: number;
  mem_limit_mi: number;
  hpa_min: number;
  hpa_max: number;
  hpa_cpu_target: number;
}

export interface App extends AppConfig {
  name: string;
  repo_url: string;
  webhook_secret: string;
  created_at: string;
  url: string;
  webhook_url: string;
  latest_deployment: Deployment | null;
}

export interface MetricSample {
  id: number;
  app_name: string;
  ts: number;
  desired_replicas: number;
  ready_replicas: number;
  cpu_m: number;
  mem_mi: number;
  hpa_util: number | null;
  hpa_target: number | null;
  rps: number | null;
}

export interface Pod {
  name: string;
  phase: string;
  ready: boolean;
  restarts: number;
  reason: string | null;
  node: string | null;
  version: string | null;
  started_at: string | null;
}

export interface HpaStatus {
  min: number;
  max: number;
  target: number | null;
  current_util: number | null;
  current_replicas: number | null;
  desired_replicas: number | null;
}

export interface DeploymentStatus {
  desired: number;
  ready: number;
  updated: number;
  available: number;
  image: string;
}

export interface LiveStatus {
  deployment: DeploymentStatus | null;
  pods: Pod[];
  hpa: HpaStatus | null;
}

export interface Prescale {
  hour: number;
  minute: number;
  duration_min: number;
  min_replicas: number;
}

export interface Recommendation {
  id: number;
  app_name: string;
  created_at: string;
  status: "new" | "applied" | "dismissed";
  rationale: string[];
  payload: Pick<
    AppConfig,
    "cpu_request_m" | "cpu_limit_m" | "mem_request_mi" | "mem_limit_mi" | "hpa_min" | "hpa_max" | "hpa_cpu_target"
  > & {
    prescale: Prescale | null;
    stats: Record<string, number | null>;
  };
}

export interface PrescaleSchedule extends Prescale {
  id: number;
  app_name: string;
  day_of_week: string;
  enabled: boolean;
}

export interface ClusterEvent {
  time: string;
  type: string;
  reason: string;
  object: string;
  message: string;
}

export type SocketMessage =
  | { type: "deployment"; deployment: Deployment }
  | { type: "build_log"; deployment_id: number; line: string }
  | { type: "event"; deployment_id: number | null; message: string }
  | { type: "pod_log"; pod: string; line: string }
  | { type: "metrics"; sample: MetricSample; pods: Pod[]; hpa: HpaStatus | null; deployment: DeploymentStatus };
