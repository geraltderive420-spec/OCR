import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import type { BatchSummary } from "../api/types";
import { fr, t } from "../i18n/fr";
import { Card, EmptyState, errText, formatDate, formatDuration, Loading, PageHeader, SegBar, useToast } from "../components/common";
import { Icon } from "../components/Icon";

interface BatchList { items: BatchSummary[]; total: number; page: number; page_size: number }

export function BatchLegend({ b }: { b: BatchSummary }) {
  const colors: Record<string, string> = { done: "var(--success)", review: "var(--warning)", running: "var(--primary)", queued: "var(--line-strong)", failed: "var(--danger)", cancelled: "var(--faint)" };
  return (
    <div className="legend">
      {(["done", "review", "running", "queued", "failed", "cancelled"] as const).map((k) => b.counts[k] ? (
        <span key={k}><i style={{ background: colors[k] }} />{fr.batch.groups[k]} <b className="num">{b.counts[k]}</b></span>
      ) : null)}
    </div>
  );
}

export const zipName = (name: string) => `${name.replace(/[^\w\- ()]/g, "_").trim() || "lot"}.zip`;

export default function Batches() {
  const toast = useToast();
  const [page, setPage] = useState(1);
  const q = useQuery({
    queryKey: ["batches", page],
    queryFn: () => api.get<BatchList>(`/batches?page=${page}&page_size=20`),
    placeholderData: keepPreviousData,
    refetchInterval: (s) => (s.state.data?.items.some((b) => b.active > 0) ? 3000 : false),
  });
  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader title={fr.batch.title} sub={fr.batch.sub} actions={<Link className="btn primary" to="/"><Icon name="upload" />{fr.nav.new}</Link>} />
      {q.isLoading && <Loading />}
      {data && data.items.length === 0 && (
        <Card><EmptyState icon="layers" title={fr.batch.empty}><p>{fr.batch.emptyHelp}</p><Link className="btn primary" to="/"><Icon name="upload" />{fr.docs.start}</Link></EmptyState></Card>
      )}
      <div className="stack">
        {data?.items.map((b) => (
          <Card key={b.id} pad={false}>
            <div className="card-body stack-sm">
              <div className="row">
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Link to={`/lots/${b.id}`} className="name" style={{ fontWeight: 650, fontSize: "1rem" }} dir="auto">{b.name}</Link>
                  <div className="muted small">{formatDate(b.created_at)} · {b.total} {fr.batch.files} · {b.pages} {fr.batch.pages}
                    {b.active > 0 && b.eta_seconds !== null && <> · <Icon name="clock" /> {t(fr.batch.eta, { t: formatDuration(b.eta_seconds) })}</>}
                  </div>
                </div>
                <b className="num" style={{ fontSize: "1.1rem" }}>{Math.round(b.progress * 100)} %</b>
                {b.exportable > 0 && (
                  <button className="small" onClick={() => downloadFile(`/batches/${b.id}/export.zip`, zipName(b.name)).catch((e) => toast(errText(e), "error"))}>
                    <Icon name="archive" /><span className="hide-sm">ZIP</span>
                  </button>
                )}
                <Link className="btn small" to={`/lots/${b.id}`}>{fr.batch.open}<Icon name="right" /></Link>
              </div>
              <SegBar counts={b.counts} total={b.total} />
              <BatchLegend b={b} />
            </div>
          </Card>
        ))}
      </div>
      {pages > 1 && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button className="small" disabled={page <= 1} onClick={() => setPage(page - 1)}><Icon name="left" />{fr.common.prev}</button>
          <span className="muted small">{page} / {pages}</span>
          <button className="small" disabled={page >= pages} onClick={() => setPage(page + 1)}>{fr.common.next}<Icon name="right" /></button>
        </div>
      )}
    </>
  );
}
