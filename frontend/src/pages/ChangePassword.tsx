import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fr } from "../i18n/fr";
import { Alert, ErrorMessage } from "../components/common";
import { Icon } from "../components/Icon";
import { AuthArt } from "./Login";

export default function ChangePassword() {
  const { changePassword, user } = useAuth();
  const nav = useNavigate();
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (newPw !== confirm) return setError(new Error(fr.password.mismatch));
    try {
      await changePassword(oldPw, newPw);
      nav("/", { replace: true });
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="auth">
      <AuthArt />
      <div className="auth-form">
        <form onSubmit={submit} className="box">
          <h2>{fr.password.title}</h2>
          {user?.must_change_password && <Alert tone="info">{fr.password.intro}</Alert>}
          <div className="form">
            <label>{fr.password.old}<input type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" required /></label>
            <label>{fr.password.next}<input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" required minLength={10} /></label>
            <label>{fr.password.confirm}<input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required /></label>
            <p className="help">{fr.password.rules}</p>
          </div>
          <ErrorMessage error={error} />
          <button className="primary lg"><Icon name="check" />{fr.password.submit}</button>
        </form>
      </div>
    </div>
  );
}
