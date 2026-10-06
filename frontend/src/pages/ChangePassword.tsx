import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fr } from "../i18n/fr";
import { Card, ErrorMessage } from "../components/common";

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
    <div className="center">
      <Card title={fr.password.title}>
        {user?.must_change_password && <p>{fr.password.intro}</p>}
        <form onSubmit={submit} className="form">
          <label>{fr.password.old}<input type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" required /></label>
          <label>{fr.password.next}<input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" required minLength={10} /></label>
          <label>{fr.password.confirm}<input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required /></label>
          <p className="muted">{fr.password.rules}</p>
          <ErrorMessage error={error} />
          <button className="primary">{fr.password.submit}</button>
        </form>
      </Card>
    </div>
  );
}
