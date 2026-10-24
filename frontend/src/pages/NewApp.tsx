import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorText } from "../api";

const DEFAULTS = {
  name: "",
  repo_url: "",
  branch: "main",
  port: 8080,
  health_path: "/healthz",
  cpu_request_m: 100,
  cpu_limit_m: 500,
  mem_request_mi: 128,
  mem_limit_mi: 256,
  hpa_min: 1,
  hpa_max: 5,
  hpa_cpu_target: 50,
};

type Form = typeof DEFAULTS;

export default function NewApp() {
  const navigate = useNavigate();
  const [form, setForm] = useState<Form>(DEFAULTS);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const num = (key: keyof Form) => ({
    type: "number",
    value: form[key] as number,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => set(key, Number(e.target.value) as never),
  });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/apps", { method: "POST", body: form });
      navigate(`/apps/${form.name}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <h2>Deploy a new app</h2>
      </div>
      <form className="card form" onSubmit={submit}>
        <section>
          <h3>Source</h3>
          <p className="muted">
            A public HTTPS Git repository with a <code>Dockerfile</code> at its root. The app must listen on the port below and
            answer the health check path with HTTP 200.
          </p>
          <div className="grid-2">
            <label>
              App name
              <input
                required
                pattern="[a-z]([-a-z0-9]{0,30}[a-z0-9])?"
                title="lowercase letters, digits and dashes; starts with a letter"
                placeholder="my-app"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
              />
              <span className="hint">Becomes the URL: name.&lt;ip&gt;.nip.io</span>
            </label>
            <label>
              Branch
              <input required value={form.branch} onChange={(e) => set("branch", e.target.value)} />
            </label>
          </div>
          <label>
            Git repository URL
            <input
              required
              type="url"
              placeholder="https://github.com/user/repo.git"
              value={form.repo_url}
              onChange={(e) => set("repo_url", e.target.value)}
            />
          </label>
          <div className="grid-2">
            <label>
              Container port
              <input {...num("port")} min={1} max={65535} />
            </label>
            <label>
              Health check path
              <input value={form.health_path} onChange={(e) => set("health_path", e.target.value)} />
            </label>
          </div>
        </section>

        <section>
          <h3>Resources</h3>
          <p className="muted">HPA CPU utilisation is measured against the CPU request.</p>
          <div className="grid-4">
            <label>
              CPU request (m)
              <input {...num("cpu_request_m")} min={10} />
            </label>
            <label>
              CPU limit (m)
              <input {...num("cpu_limit_m")} min={10} />
            </label>
            <label>
              Memory request (Mi)
              <input {...num("mem_request_mi")} min={16} />
            </label>
            <label>
              Memory limit (Mi)
              <input {...num("mem_limit_mi")} min={16} />
            </label>
          </div>
        </section>

        <section>
          <h3>Autoscaling</h3>
          <div className="grid-4">
            <label>
              Min replicas
              <input {...num("hpa_min")} min={1} max={20} />
            </label>
            <label>
              Max replicas
              <input {...num("hpa_max")} min={1} max={50} />
            </label>
            <label>
              Target CPU (%)
              <input {...num("hpa_cpu_target")} min={10} max={95} />
            </label>
          </div>
        </section>

        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button className="button primary" disabled={busy}>
            {busy ? "Starting…" : "Deploy"}
          </button>
        </div>
      </form>
    </>
  );
}
