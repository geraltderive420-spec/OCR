import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import { ACTIVE_STATUSES, DocSummary } from "../api/types";
import { fr } from "../i18n/fr";
import { Card, ErrorMessage, formatDate, Loading, ProgressBar, StatusBadge } from "../components/common";

interface Page {
  items: DocSummary[];
  total: number;
  page: number;
  page_size: number;
}

export default function History() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const q = useQuery({
    queryKey: ["documents", page],
    queryFn: () => api.get<Page>(`/documents?page=${page}&page_size=20`),
    refetchInterval: (query) => (query.state.data?.items.some((d) => ACTIVE_STATUSES.includes(d.status)) ? 2000 : false),
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/documents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  if (q.isLoading) return <Loading />;
  const data = q.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <Card title={fr.history.title}>
      <ErrorMessage error={q.error ?? del.error} />
      {data && data.items.length === 0 && <p className="muted">{fr.history.empty}</p>}
      {data && data.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>{fr.history.name}</th><th>{fr.history.pages}</th><th>{fr.history.date}</th><th>{fr.history.status}</th><th /></tr>
            </thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.id}>
                  <td dir="auto"><Link to={`/documents/${d.id}`}>{d.filename}</Link></td>
                  <td>{d.page_count}</td>
                  <td>{formatDate(d.created_at)}</td>
                  <td>
                    <StatusBadge status={d.status} />
                    {ACTIVE_STATUSES.includes(d.status) && <ProgressBar value={d.progress} />}
                  </td>
                  <td className="actions">
                    {d.has_export && <button className="link" onClick={() => downloadFile(`/documents/${d.id}/export.docx`, d.filename.replace(/\.pdf$/i, "") + ".docx")}>{fr.history.download}</button>}
                    <button className="link danger" onClick={() => window.confirm(fr.history.confirmDelete) && del.mutate(d.id)}>{fr.history.delete}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="pager">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button>
        </div>
      )}
    </Card>
  );
}
