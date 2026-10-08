import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ACTIVE_STATUSES } from "../api/types";
import { fr } from "../i18n/fr";
import { Icon, IconName, Spinner } from "./Icon";

// ------------------------------------------------------------------ statuts et avancement

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{fr.status[status] ?? status}</span>;
}

export function ProgressBar({ value, active, tone, size, label = true }: {
  value: number; active?: boolean; tone?: "success" | "danger"; size?: "lg"; label?: boolean;
}) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const bar = (
    <div className={`progress ${active ? "active" : ""} ${tone ?? ""} ${size ?? ""}`} role="progressbar"
      aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
  if (!label) return bar;
  return <div className="progress-row">{bar}<span className="pct">{pct} %</span></div>;
}

/** Barre segmentée d'un lot : terminés, à relire, en cours, échecs, annulés. */
export function SegBar({ counts, total }: { counts: Record<string, number>; total: number }) {
  const order = ["done", "review", "running", "failed", "cancelled"];
  return (
    <div className="segbar" role="img" aria-label={order.map((k) => `${fr.batch.groups[k]} : ${counts[k] ?? 0}`).join(", ")}>
      {order.map((k) => (counts[k] ? <i key={k} className={`s-${k}`} style={{ width: `${(100 * counts[k]) / Math.max(1, total)}%` }} /> : null))}
    </div>
  );
}

export function isActive(status: string) {
  return ACTIVE_STATUSES.includes(status);
}

// ------------------------------------------------------------------ mise en page

export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: ReactNode }) {
  return (
    <header className="page-head">
      <div style={{ minWidth: 0 }}>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <h1 dir="auto">{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function Card({ title, icon, hint, children, actions, footer, pad = true, className = "" }: {
  title?: ReactNode; icon?: IconName; hint?: ReactNode; children?: ReactNode; actions?: ReactNode; footer?: ReactNode;
  pad?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div style={{ minWidth: 0 }}>
            {title && <h2>{icon && <Icon name={icon} />}{title}</h2>}
            {hint && <p className="hint">{hint}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
      {footer && <footer className="card-foot">{footer}</footer>}
    </section>
  );
}

export function Stat({ icon, value, label, tone }: { icon: IconName; value: ReactNode; label: ReactNode; tone?: string }) {
  return (
    <div className={`stat ${tone ? `t-${tone}` : ""}`}>
      <span className="ico"><Icon name={icon} /></span>
      <div><b>{value}</b><span>{label}</span></div>
    </div>
  );
}

export function EmptyState({ icon = "files", title, children }: { icon?: IconName; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      <span className="big-ico"><Icon name={icon} /></span>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Alert({ tone = "info", title, children, icon }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children?: ReactNode; icon?: IconName }) {
  const ico: IconName = icon ?? (tone === "success" ? "checkCircle" : tone === "info" ? "info" : "alert");
  return (
    <div className={`alert ${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon name={ico} />
      <div>{title && <b>{title}</b>}{children && <div className="alert-text">{children}</div>}</div>
    </div>
  );
}

export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  return <Alert tone="danger">{error instanceof Error ? error.message : fr.common.error}</Alert>;
}

export function Loading({ text }: { text?: string }) {
  return <div className="loading"><Spinner />{text ?? fr.common.loading}</div>;
}

// ------------------------------------------------------------------ contrôles

export function Switch({ checked, onChange, label, help, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; help?: ReactNode; disabled?: boolean;
}) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      <span className="txt"><b>{label}</b>{help && <small>{help}</small>}</span>
    </label>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, full, ariaLabel }: {
  value: T; options: { value: T; label: ReactNode; title?: string }[]; onChange: (v: T) => void; full?: boolean; ariaLabel?: string;
}) {
  return (
    <div className={`segmented ${full ? "full" : ""}`} role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value} title={o.title}
          className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function FileIcon({ name }: { name: string }) {
  const img = /\.(jpe?g|png|tiff?)$/i.test(name);
  return <span className={`file-ico ${img ? "img" : ""}`}><Icon name={img ? "image" : "file"} /></span>;
}

// ------------------------------------------------------------------ fenêtre de confirmation

export function Modal({ title, icon, children, footer, onClose }: { title: ReactNode; icon?: IconName; children?: ReactNode; footer?: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true">
        <div className="modal-body"><h2>{icon && <Icon name={icon} />}{title}</h2>{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

interface ConfirmOpts { title: string; message?: ReactNode; confirm?: string; danger?: boolean; input?: { label: string; type?: string; minLength?: number } }
type ConfirmFn = (o: ConfirmOpts) => Promise<string | false>;
const ConfirmCtx = createContext<ConfirmFn | null>(null);

/** Remplace window.confirm / window.prompt par une fenêtre intégrée. Résout `false` si annulé, sinon la saisie (ou ""). */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: string | false) => void }) | null>(null);
  const [text, setText] = useState("");
  const ask = useCallback<ConfirmFn>((o) => new Promise((resolve) => { setText(""); setState({ ...o, resolve }); }), []);
  const close = (v: string | false) => { state?.resolve(v); setState(null); };
  const valid = !state?.input || text.length >= (state.input.minLength ?? 1);
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {state && (
        <Modal title={state.title} icon={state.danger ? "alert" : "info"} onClose={() => close(false)}
          footer={<>
            <button type="button" onClick={() => close(false)}>{fr.common.cancel}</button>
            <button type="button" className={state.danger ? "danger solid" : "primary"} disabled={!valid} onClick={() => close(text)} autoFocus={!state.input}>
              {state.confirm ?? fr.common.confirm}
            </button>
          </>}>
          {state.message && <div className="muted">{state.message}</div>}
          {state.input && (
            <form onSubmit={(e) => { e.preventDefault(); if (valid) close(text); }}>
              <label>{state.input.label}
                <input autoFocus type={state.input.type ?? "text"} value={text} onChange={(e) => setText(e.target.value)} minLength={state.input.minLength} />
              </label>
            </form>
          )}
        </Modal>
      )}
    </ConfirmCtx.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const c = useContext(ConfirmCtx);
  if (!c) throw new Error("ConfirmProvider manquant");
  return c;
}

// ------------------------------------------------------------------ notifications

interface Toast { id: number; tone: "success" | "error" | "info"; text: string }
const ToastCtx = createContext<((text: string, tone?: Toast["tone"]) => void) | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, tone: Toast["tone"] = "success") => {
    const id = ++seq.current;
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <Icon name={t.tone === "success" ? "checkCircle" : t.tone === "error" ? "xCircle" : "info"} />
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const c = useContext(ToastCtx);
  if (!c) throw new Error("ToastProvider manquant");
  return c;
}

/** Message d'une erreur d'API pour une notification. */
export function errText(e: unknown): string {
  return e instanceof Error ? e.message : fr.common.error;
}

// ------------------------------------------------------------------ images protégées, formats

/** Charge une image protégée (en-tête Authorization) et la présente via une URL locale ; libérée au démontage. */
export function useAuthedImage(path: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) {
      setUrl(null);
      return;
    }
    let alive = true;
    let objectUrl: string | null = null;
    api
      .blob(path.replace(/^\/api/, ""))
      .then((b) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(b);
        setUrl(objectUrl);
      })
      .catch(() => alive && setUrl(null));
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return url;
}

export function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} Ko`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} Mo`;
  return `${(n / 1024 ** 3).toFixed(1)} Go`;
}

export function parseDate(iso: string): Date {
  return new Date(iso + (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? "" : "Z"));
}

export function formatDate(iso: string): string {
  return parseDate(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return "moins d'une minute";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")}`;
}

export function relativeTime(iso: string): string {
  const s = (Date.now() - parseDate(iso).getTime()) / 1000;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return formatDate(iso);
}

export function useNow(intervalMs = 30_000) {
  const [, setT] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setT((x) => x + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

export function plural(n: number, one: string, many: string) {
  return `${n} ${n > 1 ? many : one}`;
}
