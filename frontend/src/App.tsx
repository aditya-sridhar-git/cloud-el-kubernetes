import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { getToken, setToken } from "./api";
import AppDetail from "./pages/AppDetail";
import AppsList from "./pages/AppsList";
import Login from "./pages/Login";
import NewApp from "./pages/NewApp";

export default function App() {
  const [token, setTokenState] = useState(getToken());

  useEffect(() => {
    const sync = () => setTokenState(getToken());
    window.addEventListener("platform:auth", sync);
    return () => window.removeEventListener("platform:auth", sync);
  }, []);

  if (!token) return <Login />;

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">⎈</span> K8s Deploy Platform
        </Link>
        <nav>
          <Link to="/apps/new" className="button primary small">
            + New app
          </Link>
          <button className="button ghost small" onClick={() => setToken(null)}>
            Sign out
          </button>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<AppsList />} />
          <Route path="/apps/new" element={<NewApp />} />
          <Route path="/apps/:name" element={<AppDetail />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
