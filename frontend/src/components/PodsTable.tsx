import { relative } from "../format";
import type { Pod } from "../types";

export default function PodsTable({ pods, onLogs }: { pods: Pod[]; onLogs?: (pod: string) => void }) {
  if (!pods.length) return <p className="muted">No pods running.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Pod</th>
          <th>Ready</th>
          <th>Phase / reason</th>
          <th>Restarts</th>
          <th>Version</th>
          <th>Started</th>
          {onLogs && <th />}
        </tr>
      </thead>
      <tbody>
        {pods.map((p) => (
          <tr key={p.name}>
            <td className="mono small">{p.name}</td>
            <td>{p.ready ? <span className="dot good" /> : <span className="dot bad" />}</td>
            <td>
              {p.phase}
              {p.reason && <span className="muted"> · {p.reason}</span>}
            </td>
            <td>{p.restarts}</td>
            <td className="mono small">{p.version ?? "—"}</td>
            <td className="muted">{relative(p.started_at)}</td>
            {onLogs && (
              <td>
                <button className="button ghost small" onClick={() => onLogs(p.name)}>
                  Logs
                </button>
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
