import { useState } from "react";
import { Link } from "react-router-dom";
import { fr, t } from "../i18n/fr";
import { useUploads } from "../uploads/UploadContext";
import { formatBytes, ProgressBar } from "./common";
import { Icon, Spinner } from "./Icon";

/** Plateau flottant : progression de tous les envois en cours, quelle que soit la page affichée. */
export default function UploadTray() {
  const { items, cancel, retry, clearFinished } = useUploads();
  const [open, setOpen] = useState(true);
  if (items.length === 0) return null;
  const active = items.filter((i) => i.status === "pending" || i.status === "uploading");
  const errors = items.filter((i) => i.status === "error").length;
  const total = items.reduce((a, i) => a + i.size, 0);
  const sent = items.reduce((a, i) => a + (i.status === "done" ? i.size : i.sent), 0);

  return (
    <aside className="tray" aria-label={fr.tray.title}>
      <div className="tray-head">
        {active.length ? <Spinner /> : <Icon name={errors ? "alert" : "checkCircle"} className={errors ? "warn" : "ok"} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{active.length ? t(fr.tray.sending, { n: active.length }) : fr.tray.finished}</b>
          {errors > 0 && <div className="small error">{t(fr.tray.errors, { n: errors })}</div>}
        </div>
        <button className="ghost icon-btn" onClick={() => setOpen(!open)} title={open ? fr.tray.collapse : fr.tray.expand} aria-label={open ? fr.tray.collapse : fr.tray.expand}>
          <Icon name={open ? "chevronDown" : "chevronUp"} />
        </button>
        {active.length === 0 && (
          <button className="ghost icon-btn" onClick={clearFinished} title={fr.tray.clear} aria-label={fr.tray.clear}><Icon name="x" /></button>
        )}
      </div>
      {active.length > 0 && <div style={{ padding: ".55rem .9rem 0" }}><ProgressBar value={total ? sent / total : 0} active /></div>}
      {open && (
        <div className="tray-body">
          {items.map((i) => (
            <div className="tray-item" key={i.id}>
              <div className="top">
                <Icon name={i.status === "done" ? "checkCircle" : i.status === "error" ? "xCircle" : i.status === "cancelled" ? "stop" : "file"}
                  className={i.status === "done" ? "ok" : i.status === "error" ? "error" : "muted"} />
                <span className="ellipsis" style={{ flex: 1 }} dir="auto" title={i.name}>
                  {i.docId ? <Link to={`/documents/${i.docId}`}>{i.name}</Link> : i.name}
                </span>
                <span className="muted small num">{i.status === "uploading" ? `${Math.round((100 * i.sent) / Math.max(1, i.size))} %` : fr.tray.state[i.status]}</span>
                {(i.status === "pending" || i.status === "uploading") && (
                  <button className="ghost icon-btn" style={{ width: 24, minHeight: 24 }} onClick={() => cancel(i.id)} title={fr.tray.cancel} aria-label={fr.tray.cancel}><Icon name="x" /></button>
                )}
                {(i.status === "error" || i.status === "cancelled") && (
                  <button className="ghost icon-btn" style={{ width: 24, minHeight: 24 }} onClick={() => retry(i.id)} title={fr.tray.retry} aria-label={fr.tray.retry}><Icon name="refresh" /></button>
                )}
              </div>
              {i.status === "uploading" && <ProgressBar value={i.sent / Math.max(1, i.size)} label={false} />}
              {i.status === "error" && <div className="small error">{i.error}</div>}
              {i.status === "pending" && <div className="small faint">{formatBytes(i.size)}</div>}
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
