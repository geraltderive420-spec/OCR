import { ReactNode, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { fr, t } from "../../i18n/fr";
import { Alert, Card, errText, Loading, ProgressBar, Segmented, Switch, useToast } from "../../components/common";
import { Icon, Spinner } from "../../components/Icon";

type Val = string | number | boolean;
interface Binary { configured: boolean; path: string | null; ok: boolean; version: string }
interface OcrInfo {
  settings: Record<string, Val>;
  defaults: Record<string, Val>;
  backend: string;
  binary: Binary;
  cpu_binary: Binary;
  models: Record<string, boolean>;
  gpu: { name: string; total_mb: number; free_mb: number; driver: string }[] | null;
  worker: { alive: boolean; state: string; engine: string } | null;
}

const WIDTHS = [0, 1500, 1800, 2000, 2200, 2500];
const e = fr.admin.engine;

function Row({ label, help, k, info, form, onReset, children }: {
  label: ReactNode; help?: ReactNode; k: string; info: OcrInfo; form: Record<string, Val>; onReset: (k: string) => void; children: ReactNode;
}) {
  const custom = info.settings[k] !== info.defaults[k] || form[k] !== info.settings[k];
  return (
    <div className="set-row">
      <div className="head">
        <span className="lbl" style={{ fontWeight: 600, fontSize: ".86rem" }}>{label}</span>
        {custom && <button type="button" className="reset-link" onClick={() => onReset(k)} title={t(e.defaultIs, { v: String(info.defaults[k] || "—") })}>{fr.admin.settings.reset}</button>}
      </div>
      {children}
      {help && <span className="help">{help}</span>}
    </div>
  );
}

function BinaryStatus({ b }: { b: Binary }) {
  if (!b.configured) return <span className="muted small"><Icon name="info" /> {b.version}</span>;
  return (
    <div className={`check-item ${b.ok ? "ok" : "ko"}`}>
      <Icon name={b.ok ? "checkCircle" : "xCircle"} />
      <div style={{ minWidth: 0 }}>
        <b className="small">{b.ok ? e.found : b.path ? b.version : e.notFound}</b>
        {b.ok && <div className="small muted">{b.version}</div>}
        {b.path && <div className="small faint ellipsis" title={b.path}>{b.path}</div>}
        {!b.path && <div className="small muted">{b.version}</div>}
      </div>
    </div>
  );
}

function PathField({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [res, setRes] = useState<Binary | null>(null);
  const check = useMutation({
    mutationFn: () => api.post<Binary>("/admin/ocr/check-llama", { path: value }),
    onSuccess: setRes,
  });
  useEffect(() => setRes(null), [value]);
  return (
    <div className="stack-sm">
      <div className="input-group">
        <input value={value} onChange={(ev) => onChange(ev.target.value)} placeholder={placeholder} spellCheck={false} dir="ltr" />
        <button type="button" onClick={() => check.mutate()} disabled={!value.trim() || check.isPending}>
          {check.isPending ? <Spinner /> : <Icon name="search" />}{check.isPending ? e.verifying : e.verify}
        </button>
      </div>
      {res && <BinaryStatus b={res} />}
    </div>
  );
}

export default function OcrEngine() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["admin-ocr"], queryFn: () => api.get<OcrInfo>("/admin/ocr"), refetchInterval: 15000 });
  const [form, setForm] = useState<Record<string, Val>>({});
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (q.data && !loaded) { setForm(q.data.settings); setLoaded(true); }
  }, [q.data, loaded]);

  const changed = useMemo(() => {
    if (!q.data) return {};
    return Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== q.data!.settings[k]));
  }, [form, q.data]);
  const dirty = Object.keys(changed).length > 0;

  const save = useMutation({
    mutationFn: () => api.put("/admin/settings", changed),
    onSuccess: async () => {
      toast(fr.admin.settings.saved);
      const fresh = await qc.fetchQuery({ queryKey: ["admin-ocr"], queryFn: () => api.get<OcrInfo>("/admin/ocr") });
      setForm(fresh.settings);
      qc.invalidateQueries({ queryKey: ["admin-settings"] });
    },
    onError: (err) => toast(errText(err), "error"),
  });
  const reset = useMutation({
    mutationFn: (k: string) => api.post("/admin/settings/reset", { keys: [k] }),
    onSuccess: async (_r, k) => {
      toast(fr.admin.settings.resetDone);
      const fresh = await qc.fetchQuery({ queryKey: ["admin-ocr"], queryFn: () => api.get<OcrInfo>("/admin/ocr") });
      setForm((f) => ({ ...f, [k]: fresh.settings[k] }));
    },
    onError: (err) => toast(errText(err), "error"),
  });

  if (q.isLoading || !loaded) return <Loading />;
  const info = q.data;
  if (!info) return <Alert tone="danger">{errText(q.error)}</Alert>;
  const set = (k: string, v: Val) => setForm((f) => ({ ...f, [k]: v }));
  const rowProps = { info, form, onReset: (k: string) => reset.mutate(k) };
  const width = Number(form.page_width);
  const gpu = info.gpu?.[0];

  return (
    <div className="stack">
      {/* état réel de l'installation */}
      <div className="grid-2">
        <Card title={e.status} icon="checkCircle">
          <div className="check-list">
            <div className={`check-item ${info.worker?.alive ? "ok" : "wa"}`}>
              <Icon name={info.worker?.alive ? "checkCircle" : "alert"} />
              <span>{e.worker} : <b>{info.worker?.alive ? e.workerOn : e.workerOff}</b>
                {info.worker?.alive && info.worker.engine !== "off" && <span className="muted"> · {info.worker.engine === "cpu" ? fr.service.cpu : fr.service.gpu}</span>}
              </span>
            </div>
            {Object.entries(info.models).map(([k, ok]) => (
              <div key={k} className={`check-item ${ok ? "ok" : k === "surya_gguf" ? "ko" : "wa"}`}>
                <Icon name={ok ? "checkCircle" : k === "surya_gguf" ? "xCircle" : "alert"} /><span>{e.models[k] ?? k}</span>
              </div>
            ))}
            <div className="check-item"><Icon name="cpu" className="muted" /><span className="muted">{e.backend} : {info.backend}</span></div>
          </div>
        </Card>
        <Card title={fr.admin.services.gpu} icon="cpu">
          {gpu ? (
            <div className="stack-sm">
              <b>{gpu.name}</b>
              <div className="meter">
                <div className="row"><span className="muted">{fr.admin.services.vram}</span><span className="num">{gpu.free_mb} / {gpu.total_mb} {fr.common.mb}</span></div>
                <ProgressBar value={1 - gpu.free_mb / Math.max(1, gpu.total_mb)} label={false} tone={gpu.free_mb < Number(form.gpu_min_free_mb) ? "danger" : undefined} />
              </div>
              <span className="muted small">Pilote {gpu.driver}</span>
            </div>
          ) : <Alert tone="warning">{e.noGpu}</Alert>}
        </Card>
      </div>

      <Card title={e.llama} icon="server" hint={e.llamaHint}>
        <div className="stack">
          <Row label={e.dir} k="llama_cpp_dir" {...rowProps}>
            <PathField value={String(form.llama_cpp_dir ?? "")} onChange={(v) => set("llama_cpp_dir", v)} placeholder="C:\OCR\llama.cpp\cuda" />
            {!changed.llama_cpp_dir && <BinaryStatus b={info.binary} />}
          </Row>
          <Row label={e.cpuDir} help={e.cpuDirHelp} k="llama_cpp_cpu_dir" {...rowProps}>
            <PathField value={String(form.llama_cpp_cpu_dir ?? "")} onChange={(v) => set("llama_cpp_cpu_dir", v)} placeholder="C:\OCR\llama.cpp\cpu" />
            {!changed.llama_cpp_cpu_dir && info.cpu_binary.configured && <BinaryStatus b={info.cpu_binary} />}
          </Row>
          <Switch checked={Boolean(form.cpu_fallback)} onChange={(v) => set("cpu_fallback", v)} label={e.cpuFallback} help={e.cpuFallbackHelp} />
        </div>
      </Card>

      <Card title={e.surya} icon="scan" hint={e.suryaHint}>
        <div className="stack">
          <Row label={e.width} help={e.widthHelp} k="page_width" {...rowProps}>
            <Segmented value={WIDTHS.includes(width) ? width : -1} onChange={(v) => v >= 0 && set("page_width", v)} ariaLabel={e.width}
              options={[...WIDTHS.map((w) => ({ value: w, label: w === 0 ? e.widthAuto : `${w} px`, title: w === 0 ? e.widthAutoHelp : undefined })),
                ...(WIDTHS.includes(width) ? [] : [{ value: -1, label: `${width} px` }])]} />
            <div className="row" style={{ maxWidth: 340 }}>
              <span className="muted small">{e.widthCustom}</span>
              <input type="number" min={1000} max={4000} step={50} style={{ width: 120 }} value={width || ""} placeholder="—"
                onChange={(ev) => set("page_width", ev.target.value === "" ? 0 : Number(ev.target.value))} />
            </div>
          </Row>
          <div className="set-grid">
            <Row label={e.parallel} help={e.parallelHelp} k="llama_parallel" {...rowProps}>
              <div className="range-row">
                <input type="range" min={1} max={8} value={Number(form.llama_parallel)} onChange={(ev) => set("llama_parallel", Number(ev.target.value))} />
                <output>{String(form.llama_parallel)}</output>
              </div>
            </Row>
            <Row label={e.ngl} help={e.nglHelp} k="llama_ngl" {...rowProps}>
              <input type="number" min={0} max={999} value={Number(form.llama_ngl)} onChange={(ev) => set("llama_ngl", Number(ev.target.value))} />
            </Row>
            <Row label={e.ctx} k="llama_ctx_size" {...rowProps}>
              <select value={Number(form.llama_ctx_size)} onChange={(ev) => set("llama_ctx_size", Number(ev.target.value))}>
                {[8192, 12288, 16384, 24576, 32768].concat([Number(form.llama_ctx_size)]).filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b)
                  .map((v) => <option key={v} value={v}>{v.toLocaleString("fr-FR")}</option>)}
              </select>
            </Row>
            <Row label={e.ceiling} help={e.ceilingHelp} k="block_token_ceiling" {...rowProps}>
              <input type="number" min={1024} max={32768} step={512} value={Number(form.block_token_ceiling)} onChange={(ev) => set("block_token_ceiling", Number(ev.target.value))} />
            </Row>
          </div>
        </div>
      </Card>

      <Card title={e.layout} icon="layers">
        <div className="stack">
          <Switch checked={Boolean(form.layout_refine)} onChange={(v) => set("layout_refine", v)} label={e.layoutRefine} help={e.layoutRefineHelp} />
          {Boolean(form.layout_refine) && (
            <div style={{ paddingInlineStart: "3.1rem" }}>
              <Row label={e.device} k="line_detector_device" {...rowProps}>
                <Segmented value={String(form.line_detector_device)} onChange={(v) => set("line_detector_device", v)}
                  options={[{ value: "cpu", label: "Processeur" }, { value: "cuda", label: "GPU (CUDA)" }]} />
              </Row>
            </div>
          )}
          <Switch checked={Boolean(form.stamp_erase)} onChange={(v) => set("stamp_erase", v)} label={e.eraseStamps} help={e.eraseStampsHelp} />
          <Switch checked={Boolean(form.layout_heron)} onChange={(v) => set("layout_heron", v)} label={e.heron} help={e.heronHelp} />
          <Switch checked={Boolean(form.second_reader)} onChange={(v) => set("second_reader", v)} label={e.second} help={e.secondHelp} />
          {Boolean(form.second_reader) && (
            <div style={{ paddingInlineStart: "3.1rem", maxWidth: 520 }}>
              <Row label={e.threshold} k="second_reader_threshold" {...rowProps}>
                <div className="range-row">
                  <input type="range" min={0.5} max={1.01} step={0.01} value={Number(form.second_reader_threshold)}
                    onChange={(ev) => set("second_reader_threshold", Number(ev.target.value))} />
                  <output>{Math.round(Number(form.second_reader_threshold) * 100)} %</output>
                </div>
              </Row>
            </div>
          )}
        </div>
      </Card>

      <Card title={e.gpu} icon="gauge">
        <div className="set-grid">
          <Row label={fr.admin.settings.labels.gpu_min_free_mb} k="gpu_min_free_mb" {...rowProps}>
            <input type="number" min={256} max={24576} step={100} value={Number(form.gpu_min_free_mb)} onChange={(ev) => set("gpu_min_free_mb", Number(ev.target.value))} />
          </Row>
          <Row label={fr.admin.settings.labels.gpu_idle_unload_minutes} k="gpu_idle_unload_minutes" {...rowProps}>
            <input type="number" min={0} max={120} value={Number(form.gpu_idle_unload_minutes)} onChange={(ev) => set("gpu_idle_unload_minutes", Number(ev.target.value))} />
          </Row>
        </div>
      </Card>

      {dirty && (
        <div className="save-bar">
          <Icon name="info" />
          <span style={{ flex: 1 }}>{fr.admin.settings.unsaved} ({Object.keys(changed).length})</span>
          <button type="button" onClick={() => setForm(info.settings)}>{fr.admin.settings.discard}</button>
          <button type="button" className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? <Spinner /> : <Icon name="check" />}{fr.admin.settings.save}
          </button>
        </div>
      )}
    </div>
  );
}
