import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import { ACTIVE_STATUSES, DocDetail } from "../api/types";
import { fr, t } from "../i18n/fr";
import { Card, ErrorMessage, Loading, ProgressBar, StatusBadge } from "../components/common";
import Review from "../components/Review";

export default function DocumentPage() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<DocDetail>(`/documents/${id}`),
    refetchInterval: (query) => (query.state.data && ACTIVE_STATUSES.includes(query.state.data.status) ? 2000 : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["document", id] });
  const finalize = useMutation({ mutationFn: () => api.post(`/documents/${id}/finalize`), onSuccess: refresh });
  const retry = useMutation({ mutationFn: () => api.post(`/documents/${id}/retry`), onSuccess: refresh });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorMessage error={q.error ?? new Error(fr.common.error)} />;
  const d = q.data;
  const active = ACTIVE_STATUSES.includes(d.status);
  const canReview = d.status === "review" || d.status === "done";
  const stats = d.options.llm_stats;

  return (
    <>
      <Card
        title={d.filename}
        actions={
          <div className="actions">
            <StatusBadge status={d.status} />
            {d.has_export && d.status === "done" && (
              <button className="primary" onClick={() => downloadFile(`/documents/${d.id}/export.docx`, d.filename.replace(/\.pdf$/i, "") + ".docx")}>{fr.doc.download}</button>
            )}
            {canReview && (
              <button className={d.status === "review" ? "primary" : ""} onClick={() => finalize.mutate()} disabled={finalize.isPending}>{fr.doc.finalize}</button>
            )}
          </div>
        }
      >
        {active && <ProgressBar value={d.progress} />}
        {d.status === "waiting_gpu" && <p className="warn">{fr.doc.waitingGpu}</p>}
        {d.status === "exporting" && <p className="muted">{fr.doc.exporting}</p>}
        {d.status === "failed" && (
          <p className="error">{d.error ?? fr.doc.failed} <button onClick={() => retry.mutate()}>{fr.doc.retry}</button></p>
        )}
        {stats?.ignoree && <p className="warn">{fr.newDoc.ollamaOff}</p>}
        {d.pending_corrections > 0 && <p className="warn">{t(fr.doc.pending, { n: d.pending_corrections })}</p>}
        <ErrorMessage error={finalize.error ?? retry.error} />
      </Card>
      {canReview && <Review doc={d} />}
    </>
  );
}
