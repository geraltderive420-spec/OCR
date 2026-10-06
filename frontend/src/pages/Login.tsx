import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fr } from "../i18n/fr";
import { Card, ErrorMessage } from "../components/common";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
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
      nav(u.must_change_password ? "/mot-de-passe" : "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <Card title={fr.login.title}>
        <form onSubmit={submit} className="form">
          <label>
            {fr.login.username}
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
          </label>
          <label>
            {fr.login.password}
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          <ErrorMessage error={error} />
          <button className="primary" disabled={busy}>{fr.login.submit}</button>
        </form>
      </Card>
    </div>
  );
}
