import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import type { DocDetail } from "../api/types";
import { fr, t } from "../i18n/fr";
import {
  Alert, Card, errText, formatBytes, formatDate, formatDuration, isActive, Loading, PageHeader, parseDate, ProgressBar,
  StatusBadge, useConfirm, useToast,
} from "../components/common";
import { Icon } from "../components/Icon";
import Review from "../components/Review";
import { docxName } from "./History";

const STEP_OF: Record<string, number> = { queued: 0, waiting_gpu: 0, preprocessing: 0, ocr: 1, correcting: 2, review: 3, exporting: 4, done: 5 };

function Steps({ d }: { d: DocDetail }) {
  const failed = d.status === "failed" || d.status === "cancelled";
  const cur = STEP_OF[d.status] ?? 0;
  const lastOk = failed ? Math.min(4, Math.floor(d.progress / 0.3)) : cur;
  return (
    <div className="steps">
      {fr.doc.steps.map((name, i) => {
        let cls = "";
        if (failed) cls = i < lastOk ? "done" : i === lastOk ? "err" : "";
        else if (i < cur || d.status === "done") cls = "done";
        else if (i === cur) cls = d.status === "review" ? "paused" : d.status === "queued" || d.status === "waiting_gpu" ? "" : "current";
        return (
          <div key={name} className={`step ${cls}`}>
            <div className="bar" />
            <span className="lab">{cls === "done" && <Icon name="check" />}{cls === "err" && <Icon name="x" />}{name}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function DocumentPage() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<DocDetail>(`/documents/${id}`),
    refetchInterval: (s) => (s.state.data && isActive(s.state.data.status) ? 2000 : false),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["document", id] });
    qc.invalidateQueries({ queryKey: ["documents"] });
  };
  const act = useMutation({
    mutationFn: (what: "finalize" | "retry" | "cancel") => api.post(`/documents/${id}/${what}`),
    onSuccess: refresh,
    onError: (e) => toast(errText(e), "error"),
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <Alert tone="danger">{errText(q.error)}</Alert>;
  const d = q.data;
  const active = isActive(d.status);
  const canReview = d.status === "review" || d.status === "done";
  const llm = d.options.llm_stats;
  const second = d.options.second_stats;
  const download = () => downloadFile(`/documents/${d.id}/export.docx`, docxName(d.filename)).catch((e) => toast(errText(e), "error"));
  const duration = d.started_at && (d.finished_at || d.updated_at) && !active
    ? (parseDate(d.finished_at ?? d.updated_at!).getTime() - parseDate(d.started_at).getTime()) / 1000 : null;

  return (
    <>
      <PageHeader
        crumbs={d.batch_id ? <><Link to="/lots">{fr.batch.title}</Link><Icon name="right" /><Link to={`/lots/${d.batch_id}`} dir="auto">{d.batch_name}</Link><Icon name="right" /></>
          : <><Link to="/documents">{fr.docs.title}</Link><Icon name="right" /></>}
        title={d.filename}
        sub={<span className="row" style={{ gap: ".9rem" }}>
          <StatusBadge status={d.cancel_requested && active ? "cancelled" : d.status} />
          <span><Icon name="file" /> {d.page_count} {fr.batch.pages}</span>
          <span className="hide-sm"><Icon name="hardDrive" /> {formatBytes(d.size_bytes)}</span>
          <span className="hide-sm"><Icon name="clock" /> {formatDate(d.created_at)}</span>
          {duration !== null && duration > 0 && <span className="hide-sm"><Icon name="zap" /> {formatDuration(duration)}</span>}
        </span>}
        actions={<>
          {active && d.status !== "exporting" && !d.cancel_requested && (
            <button className="danger" disabled={act.isPending}
              onClick={async () => (await confirm({ title: fr.doc.confirmCancel, danger: true, confirm: fr.docs.cancel })) !== false && act.mutate("cancel")}>
              <Icon name="stop" />{fr.doc.cancel}
            </button>
          )}
          {(d.status === "failed" || d.status === "cancelled") && (
            <button onClick={() => act.mutate("retry")} disabled={act.isPending}><Icon name="refresh" />{fr.doc.retry}</button>
          )}
          {canReview && (
            <button className={d.status === "review" ? "primary" : ""} onClick={() => act.mutate("finalize")} disabled={act.isPending}>
              <Icon name="wordDoc" />{fr.doc.finalize}
            </button>
          )}
          {d.has_export && d.status === "done" && <button className="primary" onClick={download}><Icon name="download" />{fr.doc.download}</button>}
        </>}
      />

      <Card>
        <div className="stack">
          <Steps d={d} />
          {active && <ProgressBar value={d.progress} active={d.status !== "queued" && d.status !== "waiting_gpu"} size="lg" />}
          {d.cancel_requested && active && <Alert tone="warning">{fr.doc.cancelling}</Alert>}
          {d.status === "queued" && d.queue_position !== null && (
            <Alert tone="info" icon="hourglass">{fr.doc.queued} · {t(fr.doc.position, { n: d.queue_position })}</Alert>
          )}
          {d.status === "waiting_gpu" && <Alert tone="warning" icon="cpu">{fr.doc.waitingGpu}</Alert>}
          {d.status === "exporting" && <Alert tone="info" icon="wordDoc">{fr.doc.exporting}</Alert>}
          {d.status === "failed" && <Alert tone="danger" title={fr.doc.failed}>{d.error}</Alert>}
          {d.status === "cancelled" && <Alert tone="info">{fr.doc.cancelled}</Alert>}
          {llm?.ignoree && <Alert tone="warning">{fr.newDoc.ollamaOff}</Alert>}
          {(d.pending_corrections > 0 || d.to_check > 0) && canReview && (
            <div className="row">
              {d.pending_corrections > 0 && <span className="badge badge-review">{t(fr.doc.pending, { n: d.pending_corrections })}</span>}
              {d.to_check > 0 && <span className="badge badge-review">{t(fr.doc.toCheck, { n: d.to_check })}</span>}
            </div>
          )}
          {(llm && !llm.ignoree && (llm.appliquees || llm.proposees || llm.rejetees)) || (second && second.relus) ? (
            <div className="muted small stack-sm">
              {llm && !llm.ignoree && (llm.appliquees || llm.proposees || llm.rejetees) ? <span><Icon name="sparkles" /> {t(fr.doc.llmStats, { a: llm.appliquees, p: llm.proposees, r: llm.rejetees })}</span> : null}
              {second && second.relus ? <span><Icon name="eye" /> {t(fr.doc.secondStats, { r: second.relus, s: second.remplaces, d: second.desaccords })}</span> : null}
            </div>
          ) : null}
        </div>
      </Card>
      {canReview && <Review doc={d} />}
    </>
  );
}
