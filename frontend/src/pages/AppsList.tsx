import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorText } from "../api";
import StatusBadge from "../components/StatusBadge";
import { relative, shortSha } from "../format";
import type { App } from "../types";

export default function AppsList() {
  const [apps, setApps] = useState<App[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = () =>
      api<App[]>("/apps")
        .then((a) => active && setApps(a))
        .catch((e) => active && setError(errorText(e)));
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!apps) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="page-head">
        <h2>Applications</h2>
      </div>
      {apps.length === 0 ? (
        <div className="card empty">
          <p>No applications yet.</p>
          <p className="muted">Deploy one from a public Git repository that contains a Dockerfile.</p>
          <Link to="/apps/new" className="button primary">
            Deploy your first app
          </Link>
        </div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Commit</th>
                <th>Replicas (min–max)</th>
                <th>Last change</th>
                <th>URL</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((a) => (
                <tr key={a.name}>
                  <td>
                    <Link to={`/apps/${a.name}`} className="strong">
                      {a.name}
                    </Link>
                    <div className="muted small">{a.repo_url}</div>
                  </td>
                  <td>
                    <StatusBadge status={a.latest_deployment?.status} />
                  </td>
                  <td className="mono">{shortSha(a.latest_deployment?.commit_sha ?? null)}</td>
                  <td>
                    {a.hpa_min}–{a.hpa_max}
                  </td>
                  <td className="muted">{relative(a.latest_deployment?.created_at ?? null)}</td>
                  <td>
                    <a href={a.url} target="_blank" rel="noreferrer">
                      open ↗
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
