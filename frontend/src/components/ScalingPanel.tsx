import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api";
import { relative } from "../format";
import type { App, AppConfig, Prescale, PrescaleSchedule, Recommendation } from "../types";

type ConfigKey = keyof AppConfig;

const FIELDS: { key: ConfigKey; label: string }[] = [
  { key: "cpu_request_m", label: "CPU request (m)" },
  { key: "cpu_limit_m", label: "CPU limit (m)" },
  { key: "mem_request_mi", label: "Memory request (Mi)" },
  { key: "mem_limit_mi", label: "Memory limit (Mi)" },
  { key: "hpa_min", label: "Min replicas" },
  { key: "hpa_max", label: "Max replicas" },
  { key: "hpa_cpu_target", label: "Target CPU (%)" },
];

function ConfigEditor({ app, onSaved }: { app: App; onSaved: () => void }) {
  const [draft, setDraft] = useState<Partial<AppConfig>>({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const changed = Object.keys(draft).length > 0;

  const save = async () => {
    setBusy(true);
    setMsg("");
    try {
      const r = await api<{ deployment: unknown }>(`/apps/${app.name}`, { method: "PATCH", body: draft });
      setDraft({});
      setMsg(r.deployment ? "Saved: rolling out the new configuration." : "Saved: applies on the next deployment.");
      onSaved();
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>Resources and autoscaling</h3>
      <div className="grid-4">
        {FIELDS.map(({ key, label }) => (
          <label key={key}>
            {label}
            <input
              type="number"
              value={(draft[key] ?? app[key]) as number}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: Number(e.target.value) }))}
            />
          </label>
        ))}
      </div>
      <div className="actions">
        {msg && <span className="muted">{msg}</span>}
        <button className="button ghost" disabled={!changed || busy} onClick={() => setDraft({})}>
          Reset
        </button>
        <button className="button primary" disabled={!changed || busy} onClick={save}>
          Save and roll out
        </button>
      </div>
    </div>
  );
}

function RecommendationCard({
  rec,
  app,
  onChange,
  onPrescale,
}: {
  rec: Recommendation;
  app: App;
  onChange: () => void;
  onPrescale: (p: Prescale) => void;
}) {
  const [error, setError] = useState("");
  const act = async (action: "apply" | "dismiss") => {
    setError("");
    try {
      await api(`/recommendations/${rec.id}/${action}`, { method: "POST" });
      onChange();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className={`rec ${rec.status}`}>
      <div className="rec-head">
        <strong>Recommendation #{rec.id}</strong>
        <span className="muted">
          {relative(rec.created_at)} · {rec.payload.stats.samples} samples
        </span>
        <span className={`badge ${rec.status === "applied" ? "good" : rec.status === "dismissed" ? "neutral" : "progress"}`}>
          {rec.status}
        </span>
      </div>
      <table className="compact">
        <thead>
          <tr>
            <th>Setting</th>
            <th>Current</th>
            <th>Recommended</th>
          </tr>
        </thead>
        <tbody>
          {FIELDS.map(({ key, label }) => {
            const next = rec.payload[key as keyof Recommendation["payload"]] as number;
            return (
              <tr key={key} className={next !== app[key] ? "changed" : ""}>
                <td>{label}</td>
                <td>{app[key]}</td>
                <td>{next}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <ul className="rationale">
        {rec.rationale.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {error && <p className="error">{error}</p>}
      {rec.status === "new" && (
        <div className="actions">
          {rec.payload.prescale && (
            <button className="button ghost" onClick={() => onPrescale(rec.payload.prescale!)}>
              Create pre-scale schedule
            </button>
          )}
          <button className="button ghost" onClick={() => act("dismiss")}>
            Dismiss
          </button>
          <button className="button primary" onClick={() => act("apply")}>
            Apply
          </button>
        </div>
      )}
    </div>
  );
}

function PrescaleEditor({ app, schedules, reload }: { app: App; schedules: PrescaleSchedule[]; reload: () => void }) {
  const [form, setForm] = useState({ day_of_week: "*", hour: 9, minute: 0, duration_min: 60, min_replicas: 2 });
  const [error, setError] = useState("");

  const add = async () => {
    setError("");
    try {
      await api(`/apps/${app.name}/prescale`, { method: "POST", body: form });
      reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const remove = async (id: number) => {
    await api(`/prescale/${id}`, { method: "DELETE" });
    reload();
  };
  const num = (key: keyof typeof form) => ({
    type: "number",
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: Number(e.target.value) })),
  });

  return (
    <div className="card">
      <h3>Pre-scale schedules</h3>
      <p className="muted">
        Raise minReplicas ahead of a known peak so pods are already running when load arrives; the floor is restored when the
        window ends.
      </p>
      {schedules.length > 0 && (
        <table className="compact">
          <thead>
            <tr>
              <th>Days</th>
              <th>Start</th>
              <th>Duration</th>
              <th>Min replicas</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => (
              <tr key={s.id}>
                <td>{s.day_of_week}</td>
                <td>
                  {String(s.hour).padStart(2, "0")}:{String(s.minute).padStart(2, "0")}
                </td>
                <td>{s.duration_min} min</td>
                <td>{s.min_replicas}</td>
                <td>
                  <button className="button ghost small" onClick={() => remove(s.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="grid-5">
        <label>
          Days (cron)
          <input value={form.day_of_week} onChange={(e) => setForm((f) => ({ ...f, day_of_week: e.target.value }))} />
        </label>
        <label>
          Hour
          <input {...num("hour")} min={0} max={23} />
        </label>
        <label>
          Minute
          <input {...num("minute")} min={0} max={59} />
        </label>
        <label>
          Duration (min)
          <input {...num("duration_min")} min={1} />
        </label>
        <label>
          Min replicas
          <input {...num("min_replicas")} min={1} />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="button primary" onClick={add}>
          Add schedule
        </button>
      </div>
    </div>
  );
}

export default function ScalingPanel({ app, onConfigChanged }: { app: App; onConfigChanged: () => void }) {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [schedules, setSchedules] = useState<PrescaleSchedule[]>([]);
  const [windowMin, setWindowMin] = useState(30);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    api<Recommendation[]>(`/apps/${app.name}/recommendations`).then(setRecs).catch(() => {});
    api<PrescaleSchedule[]>(`/apps/${app.name}/prescale`).then(setSchedules).catch(() => {});
  }, [app.name]);

  useEffect(reload, [reload]);

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/apps/${app.name}/recommendations?window_min=${windowMin}`, { method: "POST" });
      reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const createPrescale = async (p: Prescale) => {
    try {
      await api(`/apps/${app.name}/prescale`, { method: "POST", body: { ...p, day_of_week: "*" } });
      reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="stack">
      <ConfigEditor app={app} onSaved={onConfigChanged} />
      <div className="card">
        <h3>Workload-aware recommendations</h3>
        <p className="muted">
          Analyses the collected CPU, memory, request-rate and HPA samples and suggests requests/limits, a replica range and a
          utilisation target. Run load against the app first so there is something to learn from.
        </p>
        <div className="actions left">
          <label className="inline">
            Analyse the last
            <select value={windowMin} onChange={(e) => setWindowMin(Number(e.target.value))}>
              {[10, 30, 60, 180, 1440].map((m) => (
                <option key={m} value={m}>
                  {m < 60 ? `${m} min` : `${m / 60} h`}
                </option>
              ))}
            </select>
          </label>
          <button className="button primary" onClick={generate} disabled={busy}>
            {busy ? "Analysing…" : "Generate recommendation"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {recs.map((r) => (
          <RecommendationCard
            key={r.id}
            rec={r}
            app={app}
            onChange={() => {
              reload();
              onConfigChanged();
            }}
            onPrescale={createPrescale}
          />
        ))}
      </div>
      <PrescaleEditor app={app} schedules={schedules} reload={reload} />
    </div>
  );
}
