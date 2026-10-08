import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import type { DocPage, DocSummary } from "../api/types";
import { fr, t } from "../i18n/fr";
import {
  Card, EmptyState, errText, FileIcon, formatDate, isActive, Loading, PageHeader, ProgressBar, StatusBadge,
  useConfirm, useToast,
} from "../components/common";
import { Icon } from "../components/Icon";

const PAGE_SIZE = 20;
export const docxName = (name: string) => name.replace(/\.(pdf|jpe?g|png|tiff?)$/i, "") + ".docx";

/** Actions d'un document dans une liste (téléchargement, annulation, relance, suppression). */
export function DocActions({ d, onChange }: { d: DocSummary; onChange: () => void }) {
  const confirm = useConfirm();
  const toast = useToast();
  const act = useMutation({
    mutationFn: ({ what }: { what: "cancel" | "retry" | "delete" }) =>
      what === "delete" ? api.del(`/documents/${d.id}`) : api.post(`/documents/${d.id}/${what}`),
    onSuccess: (_r, v) => { if (v.what === "delete") toast(fr.docs.deleted); onChange(); },
    onError: (e) => toast(errText(e), "error"),
  });
  const busy = ["preprocessing", "ocr", "correcting", "exporting"].includes(d.status);
  return (
    <div className="actions">
      {d.has_export && (
        <button className="ghost icon-btn" title={fr.docs.download} aria-label={fr.docs.download}
          onClick={() => downloadFile(`/documents/${d.id}/export.docx`, docxName(d.filename)).catch((e) => toast(errText(e), "error"))}>
          <Icon name="download" />
        </button>
      )}
      {isActive(d.status) && d.status !== "exporting" && !d.cancel_requested && (
        <button className="ghost icon-btn" title={fr.docs.cancel} aria-label={fr.docs.cancel} disabled={act.isPending}
          onClick={async () => (await confirm({ title: fr.doc.confirmCancel, confirm: fr.docs.cancel, danger: true })) !== false && act.mutate({ what: "cancel" })}>
          <Icon name="stop" />
        </button>
      )}
      {(d.status === "failed" || d.status === "cancelled") && (
        <button className="ghost icon-btn" title={fr.docs.retry} aria-label={fr.docs.retry} disabled={act.isPending} onClick={() => act.mutate({ what: "retry" })}>
          <Icon name="refresh" />
        </button>
      )}
      <button className="ghost icon-btn danger" title={fr.docs.delete} aria-label={fr.docs.delete} disabled={busy || act.isPending}
        onClick={async () => (await confirm({ title: fr.docs.confirmDelete, confirm: fr.docs.delete, danger: true })) !== false && act.mutate({ what: "delete" })}>
        <Icon name="trash" />
      </button>
    </div>
  );
}

export function DocTable({ items, onChange, showBatch = true }: { items: DocSummary[]; onChange: () => void; showBatch?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>{fr.docs.name}</th><th className="hide-sm">{fr.docs.pages}</th><th className="hide-sm">{fr.docs.date}</th><th>{fr.docs.status}</th><th /></tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id}>
              <td>
                <div className="doc-cell">
                  <FileIcon name={d.filename} />
                  <div style={{ minWidth: 0 }}>
                    <Link className="name" to={`/documents/${d.id}`} dir="auto">{d.filename}</Link>
                    {showBatch && d.batch_name && <div className="meta"><Link className="tag" to={`/lots/${d.batch_id}`}><Icon name="layers" />{d.batch_name}</Link></div>}
                    {d.status === "failed" && d.error && <div className="meta error">{d.error}</div>}
                  </div>
                </div>
              </td>
              <td className="hide-sm num">{d.page_count}</td>
              <td className="hide-sm muted small">{formatDate(d.created_at)}</td>
              <td style={{ width: 210 }}>
                <div className="status-cell">
                  <StatusBadge status={d.cancel_requested && isActive(d.status) ? "cancelled" : d.status} />
                  {isActive(d.status) && <ProgressBar value={d.progress} active={d.status !== "queued" && d.status !== "waiting_gpu"} />}
                </div>
              </td>
              <td><DocActions d={d} onChange={onChange} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function History() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filter = params.get("statut") ?? "all";
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => { setQ(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(id);
  }, [search]);

  const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (q) query.set("q", q);
  if (filter !== "all") query.set("status", filter);
  const docs = useQuery({
    queryKey: ["documents", page, filter, q],
    queryFn: () => api.get<DocPage>(`/documents?${query}`),
    placeholderData: keepPreviousData,
    refetchInterval: (s) => (s.state.data?.items.some((d) => isActive(d.status)) ? 2500 : false),
  });
  const reload = () => qc.invalidateQueries({ queryKey: ["documents"] });

  const data = docs.data;
  const counts = data?.counts ?? {};
  const all = Object.values(counts).reduce((a, b) => a + b, 0);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader title={fr.docs.title} sub={fr.docs.sub}
        actions={<Link className="btn primary" to="/"><Icon name="upload" />{fr.nav.new}</Link>} />
      <Card pad={false}>
        <div className="card-head">
          <div className="chips">
            {Object.entries(fr.docs.filters).map(([k, label]) => (
              <button key={k} className={`chip-filter ${filter === k ? "on" : ""}`}
                onClick={() => { setParams(k === "all" ? {} : { statut: k }); setPage(1); }}>
                {label}<span className="n">{k === "all" ? all : counts[k] ?? 0}</span>
              </button>
            ))}
          </div>
          <div className="input-icon" style={{ width: 260, maxWidth: "100%" }}>
            <Icon name="search" />
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={fr.docs.search} aria-label={fr.docs.search} />
          </div>
        </div>
        {docs.isLoading && <Loading />}
        {data && data.items.length === 0 && (
          <EmptyState icon="files" title={all === 0 ? fr.docs.emptyAll : fr.docs.empty}>
            {all === 0 && <Link className="btn primary" to="/"><Icon name="upload" />{fr.docs.start}</Link>}
          </EmptyState>
        )}
        {data && data.items.length > 0 && <DocTable items={data.items} onChange={reload} />}
        {data && data.total > PAGE_SIZE && (
          <div className="pager">
            <span>{t(fr.docs.pageOf, { from: (page - 1) * PAGE_SIZE + 1, to: Math.min(page * PAGE_SIZE, data.total), total: data.total })}</span>
            <div className="row">
              <button className="small" disabled={page <= 1} onClick={() => setPage(page - 1)}><Icon name="left" />{fr.common.prev}</button>
              <button className="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>{fr.common.next}<Icon name="right" /></button>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}
