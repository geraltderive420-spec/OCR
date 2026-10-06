import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { fr } from "../i18n/fr";

export default function Layout() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  return (
    <>
      <header className="topbar">
        <strong className="brand">{fr.appName}</strong>
        <nav aria-label="Navigation principale">
          <NavLink to="/" end>{fr.nav.new}</NavLink>
          <NavLink to="/historique">{fr.nav.history}</NavLink>
          {user?.role === "admin" && <NavLink to="/administration">{fr.nav.admin}</NavLink>}
        </nav>
        <span className="spacer" />
        <span className="muted">{user?.username}</span>
        <button className="link" onClick={() => logout().then(() => nav("/connexion"))}>{fr.nav.logout}</button>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </>
  );
}
