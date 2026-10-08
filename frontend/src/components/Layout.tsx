import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServiceStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { fr, t } from "../i18n/fr";
import { Icon } from "./Icon";
import UploadTray from "./UploadTray";

type Theme = "auto" | "light" | "dark";

function readTheme(): Theme {
  try {
    const v = localStorage.getItem("ocr-theme");
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

function applyTheme(th: Theme) {
  if (th === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", th);
  try {
    localStorage.setItem("ocr-theme", th);
  } catch {
    /* stockage indisponible : le thème vaut pour la session */
  }
}

function ServicePill() {
  const q = useQuery({ queryKey: ["status"], queryFn: () => api.get<ServiceStatus>("/status"), refetchInterval: 8000, retry: 0 });
  const s = q.data;
  if (!s) return null;
  const working = s.worker_state === "working";
  const cls = !s.worker_alive ? "off" : working ? "busy" : "on";
  const engine = s.engine === "cpu" ? fr.service.cpu : s.engine === "off" ? fr.service.off : fr.service.gpu;
  return (
    <div className="svc" title={!s.worker_alive ? fr.service.downHelp : `${fr.admin.engine.backend} : ${engine}`}>
      <span className={`dot ${cls}`} />
      <div style={{ minWidth: 0 }}>
        <b>{!s.worker_alive ? fr.service.down : working ? fr.service.working : fr.service.ok}</b>
        <div className="small">{t(fr.service.queue, { n: s.queue })}</div>
      </div>
    </div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<ServiceStatus>("/status"), refetchInterval: 8000, retry: 0 });

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => setOpen(false), [loc.pathname]);

  const nextTheme: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };
  const themeIcon = theme === "auto" ? "monitor" : theme === "light" ? "sun" : "moon";
  const mine = status.data?.my_active ?? 0;

  return (
    <div className={`shell ${open ? "nav-open" : ""}`}>
      <div className="mobile-bar">
        <button className="side-btn" onClick={() => setOpen(true)} aria-label={fr.nav.menu}><Icon name="menu" /></button>
        <strong>{fr.appName}</strong>
      </div>
      <aside className="sidebar" onClick={(e) => e.target === e.currentTarget && setOpen(false)}>
        <div className="brand">
          <span className="brand-mark"><Icon name="scan" /></span>
          <div><strong>{fr.appName}</strong><span>{fr.appTagline}</span></div>
        </div>
        <nav className="nav" aria-label="Navigation principale">
          <div className="nav-label">{fr.nav.work}</div>
          <NavLink to="/" end><Icon name="upload" />{fr.nav.new}</NavLink>
          <NavLink to="/documents"><Icon name="files" />{fr.nav.documents}{mine > 0 && <span className="count">{mine}</span>}</NavLink>
          <NavLink to="/lots"><Icon name="layers" />{fr.nav.batches}</NavLink>
          {user?.role === "admin" && (<>
            <div className="nav-label">{fr.nav.manage}</div>
            <NavLink to="/administration"><Icon name="shield" />{fr.nav.admin}</NavLink>
          </>)}
        </nav>
        <div className="side-foot">
          <ServicePill />
          <div className="me">
            <span className="avatar">{user?.username.slice(0, 2)}</span>
            <div className="who"><b className="ellipsis">{user?.username}</b><span>{user?.role === "admin" ? fr.admin.users.admin : fr.admin.users.user}</span></div>
            <button className="side-btn" title={t(fr.nav.theme, { name: fr.nav.themes[theme] })} aria-label={t(fr.nav.theme, { name: fr.nav.themes[theme] })}
              onClick={() => setTheme(nextTheme[theme])}><Icon name={themeIcon} /></button>
            <button className="side-btn" title={fr.nav.logout} aria-label={fr.nav.logout} onClick={() => logout().then(() => nav("/connexion"))}><Icon name="logout" /></button>
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="content" key={loc.pathname.split("/")[1]}>
          <Outlet />
        </div>
      </main>
      <UploadTray />
    </div>
  );
}
