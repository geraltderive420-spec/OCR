import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import type { BatchDetail } from "../api/types";
import { fr, t } from "../i18n/fr";
import {
  Alert, Card, EmptyState, errText, formatDate, formatDuration, isActive, Loading, PageHeader, ProgressBar, SegBar, Stat,
  useConfirm, useToast,
} from "../components/common";
import { Icon } from "../components/Icon";
import { useUploads } from "../uploads/UploadContext";
import { BatchLegend, zipName } from "./Batches";
import { DocTable } from "./History";

const FILTERS: Record<string, (s: string) => boolean> = {
  all: () => true,
  active: (s) => isActive(s),
  review: (s) => s === "review",
  done: (s) => s === "done",
  failed: (s) => s === "failed",
  cancelled: (s) => s === "cancelled",
};

export default function BatchPage() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const toast = useToast();
  const uploads = useUploads();
  const [filter, setFilter] = useState("all");
  const mine = uploads.items.filter((i) => i.batchId === id);
  const sending = mine.filter((i) => i.status === "pending" || i.status === "uploading");

  const q = useQuery({
    queryKey: ["batch", id],
    queryFn: () => api.get<BatchDetail>(`/batches/${id}`),
    refetchInterval: (s) => ((s.state.data?.active ?? 0) > 0 || sending.length > 0 ? 2500 : false),
  });
  const reload = () => {
    qc.invalidateQueries({ queryKey: ["batch", id] });
    qc.invalidateQueries({ queryKey: ["batches"] });
    qc.invalidateQueries({ queryKey: ["documents"] });
  };
  const act = useMutation({
    mutationFn: (what: "cancel" | "retry" | "finalize" | "delete") =>
      what === "delete" ? api.del(`/batches/${id}`) : api.post<BatchDetail & { affected: number }>(`/batches/${id}/${what}`),
    onSuccess: (r, what) => {
      if (what === "delete") {
        qc.invalidateQueries({ queryKey: ["batches"] });
        nav("/lots");
        return;
      }
      toast(t(fr.batch.affected, { n: (r as { affected: number }).affected }));
      reload();
    },
    onError: (e) => toast(errText(e), "error"),
  });

  const b = q.data;
  const docs = useMemo(() => (b?.documents ?? []).filter((d) => FILTERS[filter](d.status)), [b, filter]);
  if (q.isLoading) return <Loading />;
  if (!b) return <Alert tone="danger">{errText(q.error)}</Alert>;
  const finished = b.active === 0 && sending.length === 0 && b.total > 0;

  async function ask(what: "cancel" | "retry" | "finalize" | "delete") {
    const msgs = { cancel: fr.batch.confirmCancel, finalize: fr.batch.confirmFinalize, delete: fr.batch.confirmDelete, retry: "" };
    if (what !== "retry" && (await confirm({ title: msgs[what], danger: what !== "finalize", confirm: what === "delete" ? fr.batch.deleteBatch : fr.common.confirm })) === false) return;
    act.mutate(what);
  }

  return (
    <>
      <PageHeader
        crumbs={<><Link to="/lots">{fr.batch.title}</Link><Icon name="right" /></>}
        title={b.name}
        sub={<>{formatDate(b.created_at)} · {b.total} {fr.batch.files} · {b.pages} {fr.batch.pages}</>}
        actions={<>
          <button className="primary" disabled={!b.exportable} onClick={() => downloadFile(`/batches/${id}/export.zip`, zipName(b.name)).catch((e) => toast(errText(e), "error"))}>
            <Icon name="archive" />{fr.batch.downloadZip}
          </button>
          <button className="danger" disabled={act.isPending || b.counts.running > 0} onClick={() => ask("delete")} title={fr.batch.deleteBatch}><Icon name="trash" /></button>
        </>}
      />

      <div className="stats">
        <Stat icon="files" value={b.total} label={fr.batch.total} tone="primary" />
        <Stat icon="checkCircle" value={b.counts.done} label={fr.batch.groups.done} tone="success" />
        <Stat icon="eye" value={b.counts.review} label={fr.batch.groups.review} tone="warning" />
        <Stat icon="hourglass" value={b.counts.running + b.counts.queued} label={fr.batch.groups.running} tone="info" />
        <Stat icon="xCircle" value={b.counts.failed} label={fr.batch.groups.failed} tone={b.counts.failed ? "danger" : undefined} />
      </div>

      <Card title={fr.batch.progress} icon="gauge"
        actions={<>
          {b.counts.review > 0 && <button className="small" onClick={() => ask("finalize")} disabled={act.isPending}><Icon name="wordDoc" />{t(fr.batch.finalizeAll, { n: b.counts.review })}</button>}
          {(b.counts.failed > 0 || b.counts.cancelled > 0) && <button className="small" onClick={() => ask("retry")} disabled={act.isPending}><Icon name="refresh" />{fr.batch.retryFailed}</button>}
          {b.active > 0 && <button className="small danger" onClick={() => ask("cancel")} disabled={act.isPending}><Icon name="stop" />{fr.batch.cancelAll}</button>}
        </>}>
        <div className="stack-sm">
          <div className="row">
            <b className="num" style={{ fontSize: "1.6rem" }}>{Math.round(b.progress * 100)} %</b>
            <span className="muted">
              {finished ? <><Icon name="checkCircle" className="ok" /> {fr.batch.done}</> :
                b.eta_seconds !== null ? <><Icon name="clock" /> {t(fr.batch.eta, { t: formatDuration(b.eta_seconds) })}</> : null}
            </span>
          </div>
          <SegBar counts={b.counts} total={b.total} />
          <BatchLegend b={b} />
        </div>
      </Card>

      {sending.length > 0 && (
        <Card title={fr.batch.uploads} icon="upload" hint={t(fr.batch.uploadsLeft, { n: sending.length })} pad={false}>
          <div className="tray-body" style={{ maxHeight: 260 }}>
            {sending.map((i) => (
              <div className="tray-item" key={i.id}>
                <div className="top"><Icon name="file" className="muted" /><span className="ellipsis" style={{ flex: 1 }} dir="auto">{i.name}</span>
                  <span className="muted small">{fr.tray.state[i.status]}</span></div>
                {i.status === "uploading" && <ProgressBar value={i.sent / Math.max(1, i.size)} active />}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card pad={false}>
        <div className="card-head">
          <div className="chips">
            {Object.entries(fr.docs.filters).map(([k, label]) => {
              const n = (b.documents ?? []).filter((d) => FILTERS[k](d.status)).length;
              return (
                <button key={k} className={`chip-filter ${filter === k ? "on" : ""}`} onClick={() => setFilter(k)}>
                  {label}<span className="n">{n}</span>
                </button>
              );
            })}
          </div>
        </div>
        {docs.length === 0 ? <EmptyState icon="files" title={fr.docs.empty} /> : <DocTable items={docs} onChange={reload} showBatch={false} />}
      </Card>
    </>
  );
}
