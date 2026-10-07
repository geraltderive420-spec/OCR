import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BlockData, Correction, DocDetail, PageData } from "../api/types";
import { diffWords, stripTags } from "../lib/diff";
import { fr, label } from "../i18n/fr";
import { ErrorMessage, Loading, useAuthedImage } from "./common";

const VISUAL = new Set(["Picture", "Figure", "Diagram", "Image", "Stamp"]);
// Types qu'on peut attribuer à un bloc mal détecté (miroir de EDITABLE_LABELS côté serveur).
const EDITABLE = ["Text", "SectionHeader", "ListItem", "Caption", "Footnote", "PageHeader", "PageFooter", "Table", "Picture", "Stamp"];

function kind(lbl: string): string {
  if (VISUAL.has(lbl)) return "visual";
  if (lbl === "Table") return "table";
  if (lbl === "SectionHeader" || lbl === "PageHeader" || lbl === "PageFooter") return "title";
  return "text";
}

export default function Review({ doc }: { doc: DocDetail }) {
  const qc = useQueryClient();
  const [index, setIndex] = useState(0);
  const [onlyToCheck, setOnlyToCheck] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const key = ["page", doc.id, index];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<PageData>(`/documents/${doc.id}/pages/${index}`) });
  const imgUrl = useAuthedImage(q.data?.image_url ?? null);
  const replaceBlock = (b: BlockData) =>
    qc.setQueryData<PageData>(key, (p) => (p ? { ...p, blocks: p.blocks.map((x) => (x.id === b.id ? b : x)) } : p));
  const removeBlock = (id: number) =>
    qc.setQueryData<PageData>(key, (p) => (p ? { ...p, blocks: p.blocks.filter((x) => x.id !== id) } : p));
  const refreshDoc = () => qc.invalidateQueries({ queryKey: ["document", doc.id] });

  const blocks = useMemo(() => (q.data?.blocks ?? []).filter((b) => !onlyToCheck || b.to_check || b.corrections.some((c) => c.status === "proposed")), [q.data, onlyToCheck]);

  useEffect(() => {
    if (selected === null) return;
    listRef.current?.querySelector(`[data-block="${selected}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const total = doc.page_count;
  const page = q.data;
  return (
    <div className="review">
      <div className="review-bar">
        <button onClick={() => setIndex(Math.max(0, index - 1))} disabled={index === 0} aria-label="Page précédente">‹</button>
        <span>{fr.review.page} {index + 1} {fr.review.of} {total}</span>
        <button onClick={() => setIndex(Math.min(total - 1, index + 1))} disabled={index >= total - 1} aria-label="Page suivante">›</button>
        <label className="check"><input type="checkbox" checked={onlyToCheck} onChange={(e) => setOnlyToCheck(e.target.checked)} />{fr.review.onlyToCheck}</label>
      </div>
      <ErrorMessage error={q.error} />
      {q.isLoading && <Loading />}
      {page && page.status === "error" && <p className="warn">{fr.doc.pageError}</p>}
      {page && page.status !== "error" && (
        <div className="review-grid">
          <div className="scan">
            {imgUrl ? (
              <div className="scan-inner">
                <img src={imgUrl} alt={`${fr.review.page} ${index + 1}`} />
                <svg viewBox={`0 0 ${page.width} ${page.height}`} preserveAspectRatio="none">
                  {page.blocks.map((b) => (
                    <rect
                      key={b.id}
                      x={b.bbox[0]} y={b.bbox[1]} width={b.bbox[2] - b.bbox[0]} height={b.bbox[3] - b.bbox[1]}
                      className={`box ${kind(b.label)} ${b.to_check ? "check" : ""} ${hover === b.id ? "hover" : ""} ${selected === b.id ? "sel" : ""}`}
                      onMouseEnter={() => setHover(b.id)} onMouseLeave={() => setHover(null)}
                      onClick={() => setSelected(b.id)}
                    />
                  ))}
                </svg>
              </div>
            ) : (
              <Loading />
            )}
          </div>
          <div className="blocks" ref={listRef}>
            {blocks.map((b) => (
              <BlockCard
                key={b.id}
                block={b}
                active={hover === b.id || selected === b.id}
                onHover={setHover}
                onSelect={setSelected}
                onChange={(nb) => {
                  replaceBlock(nb);
                  refreshDoc();
                }}
                onDelete={() => {
                  removeBlock(b.id);
                  refreshDoc();
                }}
              />
            ))}
            {blocks.length === 0 && <p className="muted">—</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function BlockCard({ block, active, onHover, onSelect, onChange, onDelete }: {
  block: BlockData; active: boolean; onHover: (id: number | null) => void; onSelect: (id: number) => void;
  onChange: (b: BlockData) => void; onDelete: () => void;
}) {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const editor = useRef<HTMLDivElement>(null);
  const crop = useAuthedImage(VISUAL.has(block.label) && block.has_crop ? `/blocks/${block.id}/crop` : null);

  const save = useMutation({
    mutationFn: (html: string) => api.patch<BlockData>(`/blocks/${block.id}`, { html }),
    onMutate: () => setState("saving"),
    onSuccess: (b) => {
      setState("saved");
      onChange(b);
    },
  });
  const verified = useMutation({
    mutationFn: () => api.patch<BlockData>(`/blocks/${block.id}`, { to_check: false }),
    onSuccess: onChange,
  });
  const relabel = useMutation({
    mutationFn: (lbl: string) => api.patch<BlockData>(`/blocks/${block.id}`, { label: lbl }),
    onSuccess: (b) => {
      setVersion((v) => v + 1);
      onChange(b);
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del<void>(`/blocks/${block.id}`),
    onSuccess: onDelete,
  });
  const act = useMutation({
    mutationFn: ({ c, what }: { c: Correction; what: "accept" | "reject" }) => api.post<BlockData>(`/corrections/${c.id}/${what}`),
    onSuccess: (b) => {
      setVersion((v) => v + 1); // recharge le contenu édité depuis le serveur
      onChange(b);
    },
  });

  const commit = () => {
    const html = editor.current?.innerHTML ?? "";
    if (html !== block.html) save.mutate(html);
  };

  return (
    <article
      data-block={block.id}
      className={`block ${active ? "active" : ""} ${block.to_check ? "tocheck" : ""}`}
      onMouseEnter={() => onHover(block.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(block.id)}
    >
      <header>
        <select
          className="chip"
          value={block.label}
          title={fr.review.changeType}
          aria-label={fr.review.changeType}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => relabel.mutate(e.target.value)}
          disabled={relabel.isPending}
        >
          {(EDITABLE.includes(block.label) ? EDITABLE : [block.label, ...EDITABLE]).map((l) => <option key={l} value={l}>{label(l)}</option>)}
        </select>
        {block.confidence !== null && <span className="muted">{fr.review.confidence} {Math.round(block.confidence * 100)} %</span>}
        {block.to_check && (
          <button className="chip warn" onClick={(e) => { e.stopPropagation(); verified.mutate(); }} title="Marquer comme vérifié">{fr.review.toCheck} ✓</button>
        )}
        <span className="spacer" />
        <span className="muted">{state === "saving" ? fr.review.saving : state === "saved" ? fr.review.saved : ""}</span>
        <button
          className="small"
          title={fr.review.deleteBlock}
          aria-label={fr.review.deleteBlock}
          disabled={remove.isPending}
          onClick={(e) => { e.stopPropagation(); if (window.confirm(fr.review.deleteConfirm)) remove.mutate(); }}
        >✕</button>
      </header>
      {crop && <img className="crop" src={crop} alt={label(block.label)} />}
      {!VISUAL.has(block.label) && (
        <div
          key={`${block.id}-${version}`}
          ref={editor}
          className="editable"
          contentEditable
          suppressContentEditableWarning
          dir={block.direction}
          onBlur={commit}
          dangerouslySetInnerHTML={{ __html: block.html }}
        />
      )}
      {block.corrections.filter((c) => c.status !== "rejected").map((c) => (
        <div className={`correction ${c.status}`} key={c.id}>
          <div className="muted">{c.status === "proposed" ? fr.review.proposal : c.status === "auto_applied" || c.status === "accepted" ? fr.review.applied : ""}{c.model ? ` (${c.model})` : ""}</div>
          <p dir={block.direction} className="diff">
            {diffWords(stripTags(c.before), stripTags(c.after)).map((p, i) => <span key={i} className={p.kind}>{p.text}</span>)}
          </p>
          <div className="actions">
            {c.status === "proposed" && <>
              <button className="primary small" onClick={(e) => { e.stopPropagation(); act.mutate({ c, what: "accept" }); }}>{fr.review.accept}</button>
              <button className="small" onClick={(e) => { e.stopPropagation(); act.mutate({ c, what: "reject" }); }}>{fr.review.reject}</button>
            </>}
            {(c.status === "auto_applied" || c.status === "accepted") && (
              <button className="small" onClick={(e) => { e.stopPropagation(); act.mutate({ c, what: "reject" }); }}>{fr.review.undo}</button>
            )}
          </div>
        </div>
      ))}
      <ErrorMessage error={save.error ?? act.error ?? relabel.error ?? remove.error} />
    </article>
  );
}
