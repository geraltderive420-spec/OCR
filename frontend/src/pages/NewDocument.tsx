import { FormEvent, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DocSummary, Options } from "../api/types";
import { fr, t } from "../i18n/fr";
import { Card, ErrorMessage, Loading } from "../components/common";

export default function NewDocument() {
  const nav = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const opts = useQuery({ queryKey: ["options"], queryFn: () => api.get<Options>("/options") });
  const [file, setFile] = useState<File | null>(null);
  const [preprocess, setPreprocess] = useState("auto");
  const [llm, setLlm] = useState("off");
  const [model, setModel] = useState("");
  const [llmAll, setLlmAll] = useState(false);
  const [review, setReview] = useState(true);
  const [font, setFont] = useState("");

  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("file", file as File);
      f.append("preprocess", preprocess);
      f.append("llm", llm);
      f.append("review", String(review));
      f.append("llm_all", String(llmAll));
      if (font) f.append("font", font);
      if (llm !== "off" && model) f.append("ollama_model", model);
      return api.upload<DocSummary>("/documents", f);
    },
    onSuccess: (d) => nav(`/documents/${d.id}`),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (file) upload.mutate();
  }

  if (opts.isLoading) return <Loading />;
  const o = opts.data;
  return (
    <Card title={fr.newDoc.title}>
      <form onSubmit={submit} className="form wide">
        <label>
          {fr.newDoc.file}
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
          {o && <small className="muted">{t(fr.newDoc.drop, { mb: o.max_upload_mb, pages: o.max_pages })}</small>}
        </label>
        <label>
          {fr.newDoc.preprocess}
          <select value={preprocess} onChange={(e) => setPreprocess(e.target.value)}>
            {Object.entries(fr.newDoc.preprocessOptions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>
          {fr.newDoc.font}
          <select value={font} onChange={(e) => setFont(e.target.value)}>
            <option value="">{t(fr.newDoc.fontDefault, { name: o?.default_font ?? "" })}</option>
            {o?.fonts.filter((f) => f !== o.default_font).map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <small className="muted">{fr.newDoc.fontHelp}</small>
        </label>
        <label>
          {fr.newDoc.llm}
          <select value={llm} onChange={(e) => setLlm(e.target.value)}>
            {Object.entries(fr.newDoc.llmOptions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        {llm !== "off" && (
          <>
            {o && !o.ollama_available && <p className="warn">{fr.newDoc.ollamaOff}</p>}
            <label>
              {fr.newDoc.model}
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">{t(fr.newDoc.modelDefault, { name: o?.default_model ?? "" })}</option>
                {o?.ollama_models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="check"><input type="checkbox" checked={llmAll} onChange={(e) => setLlmAll(e.target.checked)} />{fr.newDoc.llmAll}</label>
          </>
        )}
        <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} />{fr.newDoc.review}</label>
        <ErrorMessage error={upload.error} />
        <button className="primary" disabled={!file || upload.isPending}>{fr.newDoc.submit}</button>
      </form>
    </Card>
  );
}
