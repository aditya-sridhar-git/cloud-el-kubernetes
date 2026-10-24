import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import LogPane from "../components/LogPane";
import MetricsCharts from "../components/MetricsCharts";
import PodsTable from "../components/PodsTable";
import ScalingPanel from "../components/ScalingPanel";
import StatusBadge from "../components/StatusBadge";
import { relative, shortSha } from "../format";
import type { App, ClusterEvent, Deployment, LiveStatus, MetricSample, SocketMessage } from "../types";
import { useAppSocket } from "../useAppSocket";

const TABS = ["Overview", "Deployments", "Logs", "Scaling", "Settings"] as const;
type Tab = (typeof TABS)[number];
const MAX_LINES = 3000;
const MAX_SAMPLES = 480; // 2 hours at 15 s

function append<T>(list: T[], item: T, max: number): T[] {
  const next = [...list, item];
  return next.length > max ? next.slice(next.length - max) : next;
}

export default function AppDetail() {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("Overview");
  const [app, setApp] = useState<App | null>(null);
  const [error, setError] = useState("");
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [samples, setSamples] = useState<MetricSample[]>([]);
  const [live, setLive] = useState<LiveStatus>({ deployment: null, pods: [], hpa: null });
  const [liveError, setLiveError] = useState("");
  const [buildLogs, setBuildLogs] = useState<Record<number, string[]>>({});
  const [selectedDep, setSelectedDep] = useState<number | null>(null);
  const [events, setEvents] = useState<{ time: string; message: string }[]>([]);
  const [clusterEvents, setClusterEvents] = useState<ClusterEvent[]>([]);
  const [followedPod, setFollowedPod] = useState<string | null>(null);
  const [podLogs, setPodLogs] = useState<string[]>([]);
  const [showSecret, setShowSecret] = useState(false);

  const loadApp = useCallback(() => api<App>(`/apps/${name}`).then(setApp), [name]);

  useEffect(() => {
    loadApp().catch((e) => setError(errorText(e)));
    api<Deployment[]>(`/apps/${name}/deployments`).then(setDeployments).catch(() => {});
    api<MetricSample[]>(`/apps/${name}/metrics`).then(setSamples).catch(() => {});
    api<LiveStatus>(`/apps/${name}/status`)
      .then(setLive)
      .catch((e) => setLiveError(errorText(e)));
  }, [name, loadApp]);

  const onMessage = useCallback(
    (msg: SocketMessage) => {
      switch (msg.type) {
        case "deployment": {
          const d = msg.deployment;
          setDeployments((list) => [d, ...list.filter((x) => x.id !== d.id)].sort((a, b) => b.id - a.id));
          if (d.status === "QUEUED") setSelectedDep(d.id); // follow the newest build's log
          setApp((a) => (a && (!a.latest_deployment || a.latest_deployment.id <= d.id) ? { ...a, latest_deployment: d } : a));
          if (["SUCCEEDED", "FAILED", "ROLLED_BACK"].includes(d.status)) loadApp().catch(() => {});
          break;
        }
        case "build_log":
          setBuildLogs((logs) => ({ ...logs, [msg.deployment_id]: append(logs[msg.deployment_id] ?? [], msg.line, MAX_LINES) }));
          setSelectedDep((cur) => cur ?? msg.deployment_id);
          break;
        case "event":
          setEvents((list) => append(list, { time: new Date().toLocaleTimeString(), message: msg.message }, 200));
          break;
        case "metrics":
          setSamples((list) => append(list, msg.sample, MAX_SAMPLES));
          setLive({ deployment: msg.deployment, pods: msg.pods, hpa: msg.hpa });
          setLiveError("");
          break;
        case "pod_log":
          setPodLogs((lines) => append(lines, msg.line, MAX_LINES));
          break;
      }
    },
    [loadApp],
  );

  const { connected, send } = useAppSocket(name, onMessage);

  const followLogs = (pod: string) => {
    setFollowedPod(pod);
    setPodLogs([]);
    send({ type: "follow_logs", pod });
    setTab("Logs");
  };

  const action = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (e) {
      alertError(e);
    }
  };
  const alertError = (e: unknown) => setError(errorText(e));

  if (error && !app) return <p className="error">{error}</p>;
  if (!app) return <p className="muted">Loading…</p>;

  const latest = app.latest_deployment;
  const shownDep = selectedDep ?? deployments[0]?.id ?? null;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>
            {app.name} <StatusBadge status={latest?.status} />
          </h2>
          <div className="muted small">
            {app.repo_url} @ {app.branch} · <span className={connected ? "live" : "offline"}>{connected ? "live" : "reconnecting…"}</span>
          </div>
        </div>
        <div className="actions">
          <a className="button ghost" href={app.url} target="_blank" rel="noreferrer">
            Open app ↗
          </a>
          <button className="button primary" onClick={() => action(() => api(`/apps/${name}/deploy`, { method: "POST" }))}>
            Redeploy
          </button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && (
        <div className="stack">
          <div className="stats">
            <div className="stat">
              <span className="label">Ready / desired</span>
              <span className="value">
                {live.deployment ? `${live.deployment.ready} / ${live.deployment.desired}` : "—"}
              </span>
            </div>
            <div className="stat">
              <span className="label">CPU vs target</span>
              <span className="value">
                {live.hpa?.current_util ?? "—"}% <span className="muted">/ {live.hpa?.target ?? app.hpa_cpu_target}%</span>
              </span>
            </div>
            <div className="stat">
              <span className="label">HPA range</span>
              <span className="value">
                {live.hpa ? `${live.hpa.min}–${live.hpa.max}` : `${app.hpa_min}–${app.hpa_max}`}
              </span>
            </div>
            <div className="stat">
              <span className="label">Latest commit</span>
              <span className="value mono">{shortSha(latest?.commit_sha ?? null)}</span>
            </div>
          </div>
          {latest?.error && (
            <div className={`callout ${latest.status === "ROLLED_BACK" ? "warn" : "bad"}`}>
              <strong>Deployment #{latest.id}:</strong> {latest.error}
            </div>
          )}
          {liveError && <p className="muted">Live status unavailable: {liveError}</p>}
          <MetricsCharts samples={samples} />
          <div className="card">
            <h3>Pods</h3>
            <PodsTable pods={live.pods} onLogs={followLogs} />
          </div>
          <div className="card">
            <h3>Activity</h3>
            {events.length === 0 ? (
              <p className="muted">Rollout progress, rollbacks and pre-scale actions appear here as they happen.</p>
            ) : (
              <ul className="events">
                {[...events].reverse().map((e, i) => (
                  <li key={i}>
                    <span className="muted mono small">{e.time}</span> {e.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {tab === "Deployments" && (
        <div className="stack">
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Commit</th>
                  <th>Started</th>
                  <th>Details</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {deployments.map((d) => (
                  <tr key={d.id} className={d.id === shownDep ? "selected" : ""} onClick={() => setSelectedDep(d.id)}>
                    <td>{d.id}</td>
                    <td>
                      <StatusBadge status={d.status} />
                    </td>
                    <td>{d.trigger}</td>
                    <td className="mono">{shortSha(d.commit_sha)}</td>
                    <td className="muted">{relative(d.created_at)}</td>
                    <td className="small error-text">{d.error}</td>
                    <td>
                      {d.spec_json && d.status === "SUCCEEDED" && d.id !== latest?.id && (
                        <button
                          className="button ghost small"
                          onClick={(e) => {
                            e.stopPropagation();
                            action(() => api(`/apps/${name}/rollback/${d.id}`, { method: "POST" }));
                          }}
                        >
                          Roll back to this
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Build log {shownDep !== null && `#${shownDep}`}</h3>
            <LogPane
              lines={shownDep !== null ? (buildLogs[shownDep] ?? []) : []}
              empty="Build logs stream here live while a build runs. Older build logs are in the AWS CodeBuild console."
            />
          </div>
        </div>
      )}

      {tab === "Logs" && (
        <div className="card">
          <div className="actions left">
            <label className="inline">
              Pod
              <select value={followedPod ?? ""} onChange={(e) => e.target.value && followLogs(e.target.value)}>
                <option value="">select a pod…</option>
                {live.pods.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="button ghost small" onClick={() => setPodLogs([])}>
              Clear
            </button>
          </div>
          <LogPane lines={podLogs} empty="Select a pod to follow its logs." />
        </div>
      )}

      {tab === "Scaling" && <ScalingPanel app={app} onConfigChanged={() => loadApp().catch(() => {})} />}

      {tab === "Settings" && (
        <div className="stack">
          <div className="card">
            <h3>GitHub webhook (redeploy on push)</h3>
            <p className="muted">
              In the GitHub repository: Settings → Webhooks → Add webhook. Use content type <code>application/json</code>, select
              "Just the push event", and pushes to <code>{app.branch}</code> redeploy this app.
            </p>
            <dl className="kv">
              <dt>Payload URL</dt>
              <dd className="mono">{app.webhook_url}</dd>
              <dt>Secret</dt>
              <dd className="mono">
                {showSecret ? app.webhook_secret : "••••••••••••••••"}{" "}
                <button className="button ghost small" onClick={() => setShowSecret((s) => !s)}>
                  {showSecret ? "Hide" : "Show"}
                </button>
              </dd>
            </dl>
          </div>
          <div className="card">
            <h3>Cluster events</h3>
            <button
              className="button ghost small"
              onClick={() =>
                action(() =>
                  api<ClusterEvent[]>(`/apps/${name}/events`).then((ev) => setClusterEvents(ev)),
                )
              }
            >
              Load Kubernetes events
            </button>
            {clusterEvents.length > 0 && (
              <table className="compact">
                <tbody>
                  {clusterEvents.map((e, i) => (
                    <tr key={i}>
                      <td className="muted small">{relative(e.time)}</td>
                      <td className={e.type === "Warning" ? "error-text" : ""}>{e.reason}</td>
                      <td className="mono small">{e.object}</td>
                      <td className="small">{e.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="card danger">
            <h3>Delete app</h3>
            <p className="muted">Removes the Deployment, Service, Ingress and HPA, plus all history and metrics.</p>
            <DeleteButton
              name={app.name}
              onDeleted={() => navigate("/")}
              onError={alertError}
            />
          </div>
        </div>
      )}
    </>
  );
}

function DeleteButton({ name, onDeleted, onError }: { name: string; onDeleted: () => void; onError: (e: unknown) => void }) {
  const [confirm, setConfirm] = useState("");
  return (
    <div className="actions left">
      <input placeholder={`type "${name}" to confirm`} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <button
        className="button danger"
        disabled={confirm !== name}
        onClick={() =>
          api(`/apps/${name}`, { method: "DELETE" })
            .then(onDeleted)
            .catch(onError)
        }
      >
        Delete
      </button>
    </div>
  );
}
