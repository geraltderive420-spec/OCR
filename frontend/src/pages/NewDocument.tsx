import { FormEvent, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { BatchSummary, DocPage, Options } from "../api/types";
import { fr, t } from "../i18n/fr";
import {
  Alert, Card, EmptyState, ErrorMessage, FileIcon, formatBytes, isActive, Loading, PageHeader, ProgressBar, relativeTime,
  Segmented, StatusBadge, Switch, useNow,
} from "../components/common";
import Dropzone from "../components/Dropzone";
import { Icon, Spinner } from "../components/Icon";
import { UploadOptions, useUploads } from "../uploads/UploadContext";

const MAX_LIST = 500;

function extOf(name: string) {
  const m = /\.[^.]+$/.exec(name);
  return m ? m[0].toLowerCase() : "";
}

export default function NewDocument() {
  const nav = useNavigate();
  const uploads = useUploads();
  const opts = useQuery({ queryKey: ["options"], queryFn: () => api.get<Options>("/options") });
  const recent = useQuery({
    queryKey: ["documents", "recent"],
    queryFn: () => api.get<DocPage>("/documents?page=1&page_size=6"),
    refetchInterval: (q) => (q.state.data?.items.some((d) => isActive(d.status)) ? 3000 : 15000),
  });
  useNow();

  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState(0);
  const [preprocess, setPreprocess] = useState<string | null>(null);
  const [llm, setLlm] = useState("off");
  const [model, setModel] = useState("");
  const [llmAll, setLlmAll] = useState(false);
  const [review, setReview] = useState<boolean | null>(null); // null : choix automatique selon le nombre de fichiers
  const [font, setFont] = useState("");
  const [batchName, setBatchName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [single, setSingle] = useState<string | null>(null); // envoi d'un fichier seul, suivi ici

  const o = opts.data;
  const multi = files.length > 1;
  const effReview = review ?? !multi;
  const effPre = preprocess ?? o?.default_preprocess ?? "auto";
  const totalSize = useMemo(() => files.reduce((a, f) => a + f.size, 0), [files]);
  const singleItem = uploads.items.find((i) => i.id === single);

  useEffect(() => {
    if (singleItem?.status === "done" && singleItem.docId) nav(`/documents/${singleItem.docId}`);
    if (singleItem?.status === "error") { setError(new Error(singleItem.error)); setBusy(false); setSingle(null); }
  }, [singleItem, nav]);

  function addFiles(list: File[]) {
    if (!o) return;
    const ok: File[] = [];
    let bad = 0;
    for (const f of list) {
      if (!o.accepted.includes(extOf(f.name)) || f.size > o.max_upload_mb * 1024 * 1024 || f.size === 0) bad++;
      else ok.push(f);
    }
    setRejected(bad);
    setFiles((cur) => {
      const seen = new Set(cur.map((f) => `${f.name}|${f.size}|${f.lastModified}`));
      return [...cur, ...ok.filter((f) => !seen.has(`${f.name}|${f.size}|${f.lastModified}`))].slice(0, MAX_LIST);
    });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!files.length) return;
    setError(null);
    setBusy(true);
    const options: UploadOptions = { preprocess: effPre, llm, review: effReview, llm_all: llmAll, font, ollama_model: model };
    try {
      if (files.length === 1) {
        const [id] = uploads.enqueue(files, options, null);
        setSingle(id);
        return;
      }
      const name = batchName.trim() || t(fr.newDoc.batchNamePlaceholder, { date: new Date().toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) });
      const b = await api.post<BatchSummary>("/batches", { name, options });
      uploads.enqueue(files, options, b.id);
      nav(`/lots/${b.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  if (opts.isLoading) return <Loading />;
  if (!o) return <ErrorMessage error={opts.error} />;

  return (
    <>
      <PageHeader title={fr.newDoc.title} sub={fr.newDoc.sub} />
      <form onSubmit={submit} className="grid-main">
        <div className="stack">
          <Card pad={false}>
            <div className="card-body stack">
              <Dropzone onFiles={addFiles} accept={o.accepted} maxMb={o.max_upload_mb} maxPages={o.max_pages} compact={files.length > 0} />
              {rejected > 0 && <Alert tone="warning">{t(fr.newDoc.rejected, { n: rejected })}</Alert>}
              {files.length > 0 && (
                <>
                  <div className="row">
                    <b>{t(fr.newDoc.selected, { n: files.length, size: formatBytes(totalSize) })}</b>
                    <span className="spacer" />
                    <button type="button" className="ghost small" onClick={() => { setFiles([]); setRejected(0); }}><Icon name="trash" />{fr.newDoc.clear}</button>
                  </div>
                  <div className="file-list">
                    {files.map((f, i) => (
                      <div className="file-row" key={`${f.name}-${f.size}-${f.lastModified}`}>
                        <FileIcon name={f.name} />
                        <div className="grow">
                          <span className="name ellipsis" dir="auto" title={f.name}>{f.name}</span>
                          <span className="sub">{formatBytes(f.size)}</span>
                        </div>
                        <button type="button" className="ghost icon-btn" title={fr.newDoc.remove} aria-label={fr.newDoc.remove}
                          onClick={() => setFiles(files.filter((_, j) => j !== i))} disabled={busy}><Icon name="x" /></button>
                      </div>
                    ))}
                  </div>
                  {multi && (
                    <label>{fr.newDoc.batchName}
                      <input value={batchName} maxLength={120} onChange={(e) => setBatchName(e.target.value)}
                        placeholder={t(fr.newDoc.batchNamePlaceholder, { date: new Date().toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) })} />
                    </label>
                  )}
                </>
              )}
            </div>
          </Card>

          <Card title={fr.newDoc.recent} icon="clock" pad={false} actions={<Link to="/documents" className="small">{fr.newDoc.seeAll}</Link>}>
            {recent.data && recent.data.items.length === 0 && <EmptyState icon="files" title={fr.newDoc.noRecent} />}
            {recent.data && recent.data.items.length > 0 && (
              <div className="table-wrap"><table><tbody>
                {recent.data.items.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <div className="doc-cell">
                        <FileIcon name={d.filename} />
                        <div style={{ minWidth: 0 }}>
                          <Link className="name" to={`/documents/${d.id}`} dir="auto">{d.filename}</Link>
                          <div className="meta">{relativeTime(d.created_at)} · {d.page_count} p.{d.batch_name && <> · <Link className="tag" to={`/lots/${d.batch_id}`}><Icon name="layers" />{d.batch_name}</Link></>}</div>
                        </div>
                      </div>
                    </td>
                    <td style={{ width: 200 }}>
                      <div className="status-cell"><StatusBadge status={d.status} />{isActive(d.status) && <ProgressBar value={d.progress} active label={false} />}</div>
                    </td>
                  </tr>
                ))}
              </tbody></table></div>
            )}
          </Card>
        </div>

        <Card title={fr.newDoc.options} icon="sliders" pad={false}
          footer={
            <button className="primary lg" style={{ width: "100%" }} disabled={!files.length || busy}>
              {busy ? <><Spinner />{fr.newDoc.uploading}</> : <><Icon name="play" />{multi ? t(fr.newDoc.submitN, { n: files.length }) : fr.newDoc.submit}</>}
            </button>
          }>
          {singleItem && singleItem.status === "uploading" && (
            <div className="opt-section"><ProgressBar value={singleItem.sent / Math.max(1, singleItem.size)} active /></div>
          )}
          <div className="opt-section">
            <div className="field">
              <span>{fr.newDoc.preprocess}</span>
              <Segmented full value={effPre} onChange={setPreprocess} ariaLabel={fr.newDoc.preprocess}
                options={Object.entries(fr.newDoc.preprocessOptions).map(([value, label]) => ({ value, label }))} />
              <span className="help">{fr.newDoc.preprocessHelp}</span>
            </div>
            <label>{fr.newDoc.font}
              <select value={font} onChange={(e) => setFont(e.target.value)}>
                <option value="">{t(fr.newDoc.fontDefault, { name: o.default_font })}</option>
                {o.fonts.filter((f) => f !== o.default_font).map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              <span className="help" style={{ fontWeight: 400 }}>{fr.newDoc.fontHelp}</span>
            </label>
          </div>
          <div className="opt-section">
            <div className="field">
              <span>{fr.newDoc.llm}</span>
              <Segmented full value={llm} onChange={setLlm} ariaLabel={fr.newDoc.llm}
                options={Object.entries(fr.newDoc.llmOptions).map(([value, label]) => ({ value, label }))} />
              <span className="help">{fr.newDoc.llmHelp[llm]}</span>
            </div>
            {llm !== "off" && (
              <>
                {!o.ollama_available && <Alert tone="warning">{fr.newDoc.ollamaOff}</Alert>}
                <label>{fr.newDoc.model}
                  <select value={model} onChange={(e) => setModel(e.target.value)}>
                    <option value="">{t(fr.newDoc.modelDefault, { name: o.default_model })}</option>
                    {o.ollama_models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <Switch checked={llmAll} onChange={setLlmAll} label={fr.newDoc.llmAll} help={fr.newDoc.llmAllHelp} />
              </>
            )}
          </div>
          <div className="opt-section">
            <Switch checked={effReview} onChange={setReview} label={fr.newDoc.review} help={multi ? fr.newDoc.reviewBatchHelp : fr.newDoc.reviewHelp} />
          </div>
          {error != null && <div className="opt-section"><ErrorMessage error={error} /></div>}
        </Card>
      </form>
    </>
  );
}
