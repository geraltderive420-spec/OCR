import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fr } from "../i18n/fr";
import { ErrorMessage } from "../components/common";
import { Icon, Spinner } from "../components/Icon";

export function AuthArt() {
  return (
    <div className="auth-art">
      <div className="brand" style={{ padding: 0 }}>
        <span className="brand-mark"><Icon name="scan" /></span>
        <div><strong>{fr.appName}</strong><span>{fr.appTagline}</span></div>
      </div>
      <div>
        <h1>{fr.login.heroTitle}</h1>
        <p>{fr.login.heroText}</p>
        <ul>{fr.login.features.map((f) => <li key={f}><Icon name="checkCircle" />{f}</li>)}</ul>
      </div>
      <span className="small" style={{ color: "#7d8aa6" }}><Icon name="wifiOff" /> Hors ligne</span>
    </div>
  );
}

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = await login(username.trim(), password);
      nav(u.must_change_password ? "/mot-de-passe" : from && from !== "/connexion" ? from : "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <AuthArt />
      <div className="auth-form">
        <form onSubmit={submit} className="box">
          <div>
            <h2>{fr.login.title}</h2>
            <p className="muted" style={{ marginTop: ".3rem" }}>{fr.login.subtitle}</p>
          </div>
          <div className="form">
            <label>{fr.login.username}
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
            </label>
            <label>{fr.login.password}
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </label>
          </div>
          <ErrorMessage error={error} />
          <button className="primary lg" disabled={busy}>{busy ? <Spinner /> : <Icon name="key" />}{fr.login.submit}</button>
        </form>
      </div>
    </div>
  );
}
