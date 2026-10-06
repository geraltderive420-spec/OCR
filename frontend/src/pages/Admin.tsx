import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { fr } from "../i18n/fr";
import { Card, ErrorMessage, formatBytes, formatDate, Loading } from "../components/common";

type Tab = "users" | "stats" | "settings" | "services";

export default function Admin() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <>
      <h1>{fr.admin.title}</h1>
      <div className="tabs" role="tablist">
        {(Object.keys(fr.admin.tabs) as Tab[]).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{fr.admin.tabs[k]}</button>
        ))}
      </div>
      {tab === "users" && <Users />}
      {tab === "stats" && <Stats />}
      {tab === "settings" && <Settings />}
      {tab === "services" && <Services />}
    </>
  );
}

interface UserRow {
  id: number; username: string; role: string; is_active: boolean; documents: number; usage_bytes: number; last_login_at: string | null;
}

function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UserRow[]>("/admin/users") });
  const reload = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const [form, setForm] = useState({ username: "", password: "", role: "user" });
  const create = useMutation({
    mutationFn: () => api.post("/admin/users", form),
    onSuccess: () => { setForm({ username: "", password: "", role: "user" }); reload(); },
  });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: object }) => api.patch(`/admin/users/${id}`, body), onSuccess: reload });
  const reset = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) => api.post(`/admin/users/${id}/reset-password`, { password }),
    onSuccess: reload,
  });

  if (q.isLoading) return <Loading />;
  return (
    <>
      <Card>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>{fr.admin.users.username}</th><th>{fr.admin.users.role}</th><th>{fr.admin.users.docs}</th><th>{fr.admin.users.usage}</th><th>{fr.admin.users.last}</th><th /></tr>
            </thead>
            <tbody>
              {q.data?.map((u) => (
                <tr key={u.id} className={u.is_active ? "" : "off"}>
                  <td>{u.username}</td>
                  <td>{u.role === "admin" ? fr.admin.users.admin : fr.admin.users.user}</td>
                  <td>{u.documents}</td>
                  <td>{formatBytes(u.usage_bytes)}</td>
                  <td>{u.last_login_at ? formatDate(u.last_login_at) : fr.admin.users.never}</td>
                  <td className="actions">
                    <button className="link" onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? fr.admin.users.disable : fr.admin.users.enable}</button>
                    <button className="link" onClick={() => {
                      const p = window.prompt(fr.admin.users.newPassword);
                      if (p) reset.mutate({ id: u.id, password: p });
                    }}>{fr.admin.users.reset}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ErrorMessage error={patch.error ?? reset.error} />
      </Card>
      <Card title={fr.admin.users.create}>
        <form className="form inline" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
          <label>{fr.admin.users.username}<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required minLength={3} pattern="[A-Za-z0-9_.\-]+" /></label>
          <label>{fr.admin.users.password}<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={10} autoComplete="new-password" /></label>
          <label>{fr.admin.users.role}
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="user">{fr.admin.users.user}</option><option value="admin">{fr.admin.users.admin}</option>
            </select>
          </label>
          <button className="primary">{fr.admin.users.create}</button>
        </form>
        <ErrorMessage error={create.error} />
      </Card>
    </>
  );
}

function Stats() {
  const q = useQuery({ queryKey: ["admin-stats"], queryFn: () => api.get<{
    users: number; documents: number; pages_total: number; documents_par_statut: Record<string, number>;
    audit_recent: { action: string; user_id: number | null; target: string | null; at: string }[];
  }>("/admin/stats") });
  if (q.isLoading || !q.data) return <Loading />;
  const s = q.data;
  return (
    <Card>
      <dl className="kv">
        <dt>{fr.admin.stats.users}</dt><dd>{s.users}</dd>
        <dt>{fr.admin.stats.documents}</dt><dd>{s.documents}</dd>
        <dt>{fr.admin.stats.pages}</dt><dd>{s.pages_total}</dd>
        {Object.entries(s.documents_par_statut).map(([k, v]) => (<><dt key={`k${k}`}>{fr.status[k] ?? k}</dt><dd key={`v${k}`}>{v}</dd></>))}
      </dl>
      <h3>{fr.admin.stats.audit}</h3>
      <div className="table-wrap"><table><tbody>
        {s.audit_recent.map((a, i) => (<tr key={i}><td>{formatDate(a.at)}</td><td>{a.action}</td><td>{a.user_id ?? "—"}</td><td>{a.target ?? ""}</td></tr>))}
      </tbody></table></div>
    </Card>
  );
}

type Settings = Record<string, string | number | boolean>;

function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-settings"], queryFn: () => api.get<Settings>("/admin/settings") });
  const models = useQuery({ queryKey: ["admin-ollama"], queryFn: () => api.get<{ available: boolean; models: { name: string }[]; current: string }>("/admin/ollama/models") });
  const [form, setForm] = useState<Settings>({});
  const [done, setDone] = useState(false);
  useEffect(() => { if (q.data) setForm(q.data); }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/admin/settings", form),
    onSuccess: (data) => { qc.setQueryData(["admin-settings"], data); setDone(true); setTimeout(() => setDone(false), 2500); },
    onError: () => setDone(false),
  });
  if (q.isLoading || !q.data) return <Loading />;

  const set = (k: string, v: string | number | boolean) => setForm({ ...form, [k]: v });
  const names = new Set(models.data?.models.map((m) => m.name));
  if (typeof form.ollama_model === "string" && form.ollama_model) names.add(form.ollama_model);

  return (
    <Card>
      <form className="form wide" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {Object.keys(q.data).map((k) => {
          const v = form[k];
          if (k === "ollama_model") {
            return (
              <label key={k}>
                {fr.admin.settings.labels[k]}
                {models.data?.available ? (
                  <select value={String(v ?? "")} onChange={(e) => set(k, e.target.value)}>{[...names].map((n) => <option key={n} value={n}>{n}</option>)}</select>
                ) : (
                  <input value={String(v ?? "")} onChange={(e) => set(k, e.target.value)} />
                )}
                <small className="muted">{models.data?.available ? fr.admin.settings.ollamaHelp : fr.admin.settings.ollamaOff}</small>
              </label>
            );
          }
          if (typeof q.data[k] === "boolean") return <label key={k} className="check"><input type="checkbox" checked={Boolean(v)} onChange={(e) => set(k, e.target.checked)} />{fr.admin.settings.labels[k]}</label>;
          if (k === "default_preprocess") {
            return (
              <label key={k}>{fr.admin.settings.labels[k]}
                <select value={String(v)} onChange={(e) => set(k, e.target.value)}>
                  {Object.entries(fr.newDoc.preprocessOptions).map(([o, l]) => <option key={o} value={o}>{l}</option>)}
                </select>
              </label>
            );
          }
          const isNum = typeof q.data[k] === "number";
          return (
            <label key={k}>{fr.admin.settings.labels[k] ?? k}
              <input type={isNum ? "number" : "text"} step="any" value={String(v ?? "")} onChange={(e) => set(k, isNum ? Number(e.target.value) : e.target.value)} />
            </label>
          );
        })}
        <ErrorMessage error={save.error} />
        {done && <p className="ok">{fr.admin.settings.saved}</p>}
        <button className="primary" disabled={save.isPending}>{fr.admin.settings.save}</button>
      </form>
    </Card>
  );
}

function Services() {
  const q = useQuery({ queryKey: ["admin-services"], queryFn: () => api.get<{
    base_de_donnees: boolean; ollama: boolean; llama_server: boolean; vram_libre_mo: number | null;
    disque_libre_mo: number; disque_total_mo: number; ocr_backend: string; file_attente: number;
  }>("/admin/services"), refetchInterval: 5000 });
  if (q.isLoading || !q.data) return <Loading />;
  const s = q.data;
  const st = fr.admin.services;
  const dot = (ok: boolean, off = st.down) => <span className={ok ? "ok" : "warn"}>{ok ? st.up : off}</span>;
  return (
    <Card>
      <dl className="kv">
        <dt>{st.database}</dt><dd>{dot(s.base_de_donnees)}</dd>
        <dt>{st.ollama}</dt><dd>{dot(s.ollama)}</dd>
        <dt>{st.llama}</dt><dd>{dot(s.llama_server, st.llamaIdle)}</dd>
        <dt>{st.vram}</dt><dd>{s.vram_libre_mo === null ? "—" : `${s.vram_libre_mo} ${fr.common.mb}`}</dd>
        <dt>{st.disk}</dt><dd>{s.disque_libre_mo} / {s.disque_total_mo} {fr.common.mb}</dd>
        <dt>{st.queue}</dt><dd>{s.file_attente}</dd>
        <dt>{st.backend}</dt><dd>{s.ocr_backend}</dd>
      </dl>
    </Card>
  );
}
