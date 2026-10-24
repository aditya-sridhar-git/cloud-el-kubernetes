import { useState } from "react";
import { api, errorText, setToken } from "../api";

export default function Login() {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/apps", { token: value.trim() });
      setToken(value.trim());
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>
          <span className="brand-mark">⎈</span> K8s Deploy Platform
        </h1>
        <p className="muted">
          Paste the API token printed by <code>scripts/deploy-platform.ps1</code>.
        </p>
        <input
          type="password"
          placeholder="API token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        {error && <p className="error">{error}</p>}
        <button className="button primary" disabled={!value.trim() || busy}>
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
