import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { fr } from "../i18n/fr";
import {
  Alert, Card, errText, formatBytes, formatDate, Loading, PageHeader, ProgressBar, Stat, StatusBadge, Switch, useConfirm,
  useToast,
} from "../components/common";
import { Icon, Spinner } from "../components/Icon";
import OcrEngine from "./admin/OcrEngine";

type Tab = "users" | "engine" | "settings" | "services" | "stats";
const TAB_ICONS: Record<Tab, "users" | "cpu" | "sliders" | "server" | "chart"> = {
  users: "users", engine: "cpu", settings: "sliders", services: "server", stats: "chart",
};

export default function Admin() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <>
      <PageHeader title={fr.admin.title} sub={fr.admin.sub} />
      <div className="tabs" role="tablist">
        {(Object.keys(fr.admin.tabs) as Tab[]).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            <Icon name={TAB_ICONS[k]} /> {fr.admin.tabs[k]}
          </button>
        ))}
      </div>
      {tab === "users" && <Users />}
      {tab === "engine" && <OcrEngine />}
      {tab === "settings" && <Settings />}
      {tab === "services" && <Services />}
      {tab === "stats" && <Stats />}
    </>
  );
}

// ------------------------------------------------------------------ utilisateurs

interface UserRow {
  id: number; username: string; role: string; is_active: boolean; documents: number; usage_bytes: number; last_login_at: string | null;
}

function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UserRow[]>("/admin/users") });
  const reload = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const empty = { username: "", password: "", role: "user" };
  const [form, setForm] = useState(empty);
  const create = useMutation({
    mutationFn: () => api.post("/admin/users", form),
    onSuccess: () => { setForm(empty); reload(); toast(fr.admin.users.created); },
    onError: (e) => toast(errText(e), "error"),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: object }) => api.patch(`/admin/users/${id}`, body),
    onSuccess: reload,
    onError: (e) => toast(errText(e), "error"),
  });
  const reset = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) => api.post(`/admin/users/${id}/reset-password`, { password }),
    onSuccess: () => { reload(); toast(fr.admin.users.resetDone); },
    onError: (e) => toast(errText(e), "error"),
  });

  if (q.isLoading) return <Loading />;
  return (
    <div className="stack">
      <Card pad={false}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>{fr.admin.users.username}</th><th>{fr.admin.users.role}</th><th className="hide-sm">{fr.admin.users.docs}</th><th className="hide-sm">{fr.admin.users.usage}</th><th className="hide-sm">{fr.admin.users.last}</th><th /></tr>
            </thead>
            <tbody>
              {q.data?.map((u) => (
                <tr key={u.id} className={u.is_active ? "" : "off"}>
                  <td><div className="doc-cell"><span className="avatar" style={{ background: "var(--primary-soft)", color: "var(--primary)" }}>{u.username.slice(0, 2)}</span>
                    <b>{u.username}</b>{!u.is_active && <span className="tag">{fr.admin.users.disabled}</span>}</div></td>
                  <td><span className={`badge plain ${u.role === "admin" ? "badge-ocr" : ""}`}>{u.role === "admin" ? fr.admin.users.admin : fr.admin.users.user}</span></td>
                  <td className="hide-sm num">{u.documents}</td>
                  <td className="hide-sm num">{formatBytes(u.usage_bytes)}</td>
                  <td className="hide-sm muted small">{u.last_login_at ? formatDate(u.last_login_at) : fr.admin.users.never}</td>
                  <td>
                    <div className="actions">
                      <button className="small" onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? fr.admin.users.disable : fr.admin.users.enable}</button>
                      <button className="small" onClick={async () => {
                        const p = await confirm({ title: `${fr.admin.users.reset} — ${u.username}`, confirm: fr.admin.users.reset,
                          input: { label: fr.admin.users.newPassword, type: "password", minLength: 10 } });
                        if (p) reset.mutate({ id: u.id, password: p });
                      }}><Icon name="key" /><span className="hide-sm">{fr.admin.users.reset}</span></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={fr.admin.users.create} icon="plus">
        <form className="form inline" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
          <label>{fr.admin.users.username}<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required minLength={3} pattern="[A-Za-z0-9_.\-]+" /></label>
          <label>{fr.admin.users.password}<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={10} autoComplete="new-password" /></label>
          <label>{fr.admin.users.role}
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="user">{fr.admin.users.user}</option><option value="admin">{fr.admin.users.admin}</option>
            </select>
          </label>
          <button className="primary" disabled={create.isPending}><Icon name="plus" />{fr.admin.users.create}</button>
        </form>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ statistiques

function Stats() {
  const q = useQuery({ queryKey: ["admin-stats"], queryFn: () => api.get<{
    users: number; documents: number; pages_total: number; documents_par_statut: Record<string, number>;
    audit_recent: { action: string; user_id: number | null; target: string | null; at: string }[];
  }>("/admin/stats") });
  if (q.isLoading || !q.data) return <Loading />;
  const s = q.data;
  return (
    <div className="stack">
      <div className="stats">
        <Stat icon="users" value={s.users} label={fr.admin.stats.users} tone="primary" />
        <Stat icon="files" value={s.documents} label={fr.admin.stats.documents} tone="info" />
        <Stat icon="layers" value={s.pages_total} label={fr.admin.stats.pages} tone="success" />
      </div>
      <div className="grid-2">
        <Card title={fr.admin.stats.byStatus} icon="chart">
          <div className="stack-sm">
            {Object.entries(s.documents_par_statut).map(([k, v]) => (
              <div className="row" key={k} style={{ justifyContent: "space-between" }}><StatusBadge status={k} /><b className="num">{v}</b></div>
            ))}
          </div>
        </Card>
        <Card title={fr.admin.stats.audit} icon="clock" pad={false}>
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}><table><tbody>
            {s.audit_recent.map((a, i) => (<tr key={i}><td className="muted small">{formatDate(a.at)}</td><td>{a.action}</td><td className="muted">{a.user_id ?? "—"}</td><td className="muted">{a.target ?? ""}</td></tr>))}
          </tbody></table></div>
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ paramètres généraux

type Settings = Record<string, string | number | boolean>;
// Réglages du moteur OCR : édités dans l'onglet « Moteur OCR ».
const OCR_KEYS = new Set(["llama_cpp_dir", "llama_cpp_cpu_dir", "llama_parallel", "llama_ngl", "llama_ctx_size", "cpu_fallback",
  "page_width", "block_token_ceiling", "layout_refine", "line_detector_device", "stamp_erase", "layout_heron", "second_reader",
  "second_reader_threshold", "gpu_min_free_mb", "gpu_idle_unload_minutes"]);
const GROUPS: Record<string, string[]> = {
  llm: ["ollama_model", "llm_think", "llm_confidence_threshold", "llm_max_change_ratio", "llm_timeout_seconds", "llm_keep_alive_seconds"],
  docs: ["default_preprocess", "max_upload_mb", "max_pages", "quota_mb_per_user"],
  word: ["docx_latin_font", "docx_arabic_font", "docx_columns"],
  security: ["retention_days", "session_idle_minutes"],
};

function Settings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["admin-settings"], queryFn: () => api.get<Settings>("/admin/settings") });
  const models = useQuery({ queryKey: ["admin-ollama"], queryFn: () => api.get<{ available: boolean; models: { name: string }[]; current: string }>("/admin/ollama/models") });
  const [form, setForm] = useState<Settings>({});
  useEffect(() => { if (q.data) setForm(q.data); }, [q.data]);
  const changed = useMemo(() => Object.fromEntries(Object.entries(form).filter(([k, v]) => q.data && v !== q.data[k])), [form, q.data]);
  const dirty = Object.keys(changed).length > 0;
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/admin/settings", changed),
    onSuccess: (data) => { qc.setQueryData(["admin-settings"], data); qc.invalidateQueries({ queryKey: ["options"] }); toast(fr.admin.settings.saved); },
    onError: (e) => toast(errText(e), "error"),
  });
  if (q.isLoading || !q.data) return <Loading />;

  const set = (k: string, v: string | number | boolean) => setForm({ ...form, [k]: v });
  const names = new Set(models.data?.models.map((m) => m.name));
  if (typeof form.ollama_model === "string" && form.ollama_model) names.add(form.ollama_model);
  const L = fr.admin.settings.labels;

  const field = (k: string) => {
    const v = form[k];
    if (v === undefined) return null;
    if (k === "ollama_model") {
      return (
        <label key={k} className="set-row">{L[k]}
          {models.data?.available ? (
            <select value={String(v)} onChange={(e) => set(k, e.target.value)}>{[...names].map((n) => <option key={n} value={n}>{n}</option>)}</select>
          ) : <input value={String(v)} onChange={(e) => set(k, e.target.value)} />}
          <span className="help" style={{ fontWeight: 400 }}>{models.data?.available ? fr.admin.settings.ollamaHelp : fr.admin.settings.ollamaOff}</span>
        </label>
      );
    }
    if (typeof q.data![k] === "boolean") return <div key={k} className="set-row"><Switch checked={Boolean(v)} onChange={(c) => set(k, c)} label={L[k]} /></div>;
    if (k === "default_preprocess") {
      return (
        <label key={k} className="set-row">{L[k]}
          <select value={String(v)} onChange={(e) => set(k, e.target.value)}>
            {Object.entries(fr.newDoc.preprocessOptions).map(([o, l]) => <option key={o} value={o}>{l}</option>)}
          </select>
        </label>
      );
    }
    const isNum = typeof q.data![k] === "number";
    return (
      <label key={k} className="set-row">{L[k] ?? k}
        <input type={isNum ? "number" : "text"} step="any" value={String(v)} onChange={(e) => set(k, isNum ? Number(e.target.value) : e.target.value)} />
      </label>
    );
  };
  const rest = Object.keys(q.data).filter((k) => !OCR_KEYS.has(k) && !Object.values(GROUPS).flat().includes(k));

  return (
    <div className="stack">
      {Object.entries(GROUPS).map(([g, keys]) => (
        <Card key={g} title={fr.admin.settings.groups[g]} icon={g === "llm" ? "sparkles" : g === "word" ? "wordDoc" : g === "docs" ? "files" : "shield"}>
          <div className="set-grid">{keys.map(field)}</div>
        </Card>
      ))}
      {rest.length > 0 && <Card><div className="set-grid">{rest.map(field)}</div></Card>}
      {dirty && (
        <div className="save-bar">
          <Icon name="info" />
          <span style={{ flex: 1 }}>{fr.admin.settings.unsaved} ({Object.keys(changed).length})</span>
          <button type="button" onClick={() => setForm(q.data!)}>{fr.admin.settings.discard}</button>
          <button type="button" className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <Spinner /> : <Icon name="check" />}{fr.admin.settings.save}
          </button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ services

interface Svc {
  base_de_donnees: boolean; ollama: boolean; llama_server: boolean; vram_libre_mo: number | null;
  disque_libre_mo: number; disque_total_mo: number; ocr_backend: string; file_attente: number;
  gpu: { name: string; total_mb: number; free_mb: number; driver: string }[] | null;
  worker: { alive: boolean; state: string; engine: string } | null;
  database: { engine: string; path?: string; size_mb?: number | null };
  hors_ligne: boolean;
}

function Services() {
  const q = useQuery({ queryKey: ["admin-services"], queryFn: () => api.get<Svc>("/admin/services"), refetchInterval: 5000 });
  if (q.isLoading || !q.data) return <Loading />;
  const s = q.data;
  const st = fr.admin.services;
  const used = 1 - s.disque_libre_mo / Math.max(1, s.disque_total_mo);
  const gpu = s.gpu?.[0];
  const state = (ok: boolean, label: string, off: string = st.down, warn = false) => (
    <span className="row" style={{ gap: ".4rem" }}><span className={`dot ${ok ? "on" : warn ? "idle" : "off"}`} /><b>{ok ? label : off}</b></span>
  );
  return (
    <div className="stack">
      <div className="svc-grid">
        <div className="svc-card"><span className="muted small">{st.worker}</span>{state(!!s.worker?.alive, st.up)}
          {s.worker?.alive && <span className="muted small">{s.worker.state === "working" ? fr.service.working : fr.service.ok}</span>}</div>
        <div className="svc-card"><span className="muted small">{st.database}</span>{state(s.base_de_donnees, st.up)}
          <span className="muted small ellipsis" title={s.database.path}>{s.database.engine === "sqlite" ? `SQLite${s.database.size_mb != null ? ` · ${s.database.size_mb} Mo` : ""}` : s.database.engine}</span></div>
        <div className="svc-card"><span className="muted small">{st.llama}</span>{state(s.llama_server, st.up, st.llamaIdle, true)}</div>
        <div className="svc-card"><span className="muted small">{st.ollama}</span>{state(s.ollama, st.up, st.down, true)}</div>
        <div className="svc-card"><span className="muted small">{st.offline}</span>{state(s.hors_ligne, st.yes, st.no, true)}</div>
        <div className="svc-card"><span className="muted small">{st.queue}</span><span className="val num">{s.file_attente} {st.docs}</span><span className="muted small">{st.backend} : {s.ocr_backend}</span></div>
      </div>
      <div className="grid-2">
        <Card title={st.gpu} icon="cpu">
          {gpu ? (
            <div className="stack-sm">
              <b>{gpu.name}</b>
              <div className="meter"><div className="row"><span className="muted">{st.vram}</span><span className="num">{gpu.free_mb} / {gpu.total_mb} {fr.common.mb}</span></div>
                <ProgressBar value={1 - gpu.free_mb / Math.max(1, gpu.total_mb)} label={false} /></div>
              <span className="muted small">Pilote {gpu.driver}</span>
            </div>
          ) : <Alert tone="warning">{fr.admin.engine.noGpu}</Alert>}
        </Card>
        <Card title={st.disk} icon="hardDrive">
          <div className="meter"><div className="row"><span className="muted">{(s.disque_libre_mo / 1024).toFixed(1)} Go libres</span><span className="num">{(s.disque_total_mo / 1024).toFixed(0)} Go</span></div>
            <ProgressBar value={used} label={false} tone={used > 0.9 ? "danger" : undefined} /></div>
        </Card>
      </div>
    </div>
  );
}
