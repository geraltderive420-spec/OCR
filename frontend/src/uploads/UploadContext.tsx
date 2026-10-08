import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, uploadWithProgress } from "../api/client";
import type { DocSummary } from "../api/types";

// File d'envoi globale : les fichiers continuent de partir pendant qu'on navigue dans l'application.
// Deux envois à la fois : le serveur reste réactif, et un gros lot avance sans saturer le réseau local.
const CONCURRENCY = 2;

export interface UploadOptions {
  preprocess: string;
  llm: string;
  review: boolean;
  llm_all: boolean;
  font: string;
  ollama_model: string;
}

export interface UploadItem {
  id: string;
  file: File;
  name: string;
  size: number;
  batchId: number | null;
  options: UploadOptions;
  status: "pending" | "uploading" | "done" | "error" | "cancelled";
  sent: number;
  error?: string;
  docId?: number;
}

interface UploadState {
  items: UploadItem[];
  enqueue: (files: File[], options: UploadOptions, batchId: number | null) => string[];
  cancel: (id: string) => void;
  retry: (id: string) => void;
  clearFinished: () => void;
}

const Ctx = createContext<UploadState | null>(null);
let seq = 0;

export function UploadProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [items, setItems] = useState<UploadItem[]>([]);
  const started = useRef(new Set<string>());
  const controllers = useRef(new Map<string, AbortController>());

  const patch = useCallback((id: string, p: Partial<UploadItem>) => {
    setItems((list) => list.map((i) => (i.id === id ? { ...i, ...p } : i)));
  }, []);

  const start = useCallback((item: UploadItem) => {
    started.current.add(item.id);
    const ctrl = new AbortController();
    controllers.current.set(item.id, ctrl);
    patch(item.id, { status: "uploading", sent: 0, error: undefined });
    const f = new FormData();
    f.append("file", item.file, item.name);
    const o = item.options;
    f.append("preprocess", o.preprocess);
    f.append("llm", o.llm);
    f.append("review", String(o.review));
    f.append("llm_all", String(o.llm_all));
    if (o.font) f.append("font", o.font);
    if (o.llm !== "off" && o.ollama_model) f.append("ollama_model", o.ollama_model);
    if (item.batchId !== null) f.append("batch_id", String(item.batchId));
    let last = 0;
    uploadWithProgress<DocSummary>("/documents", f, (sent) => {
      const now = Date.now();
      if (now - last > 150) { // au plus ~7 rafraîchissements par seconde
        last = now;
        patch(item.id, { sent });
      }
    }, ctrl.signal)
      .then((doc) => {
        patch(item.id, { status: "done", sent: item.size, docId: doc.id });
        qc.invalidateQueries({ queryKey: ["documents"] });
        qc.invalidateQueries({ queryKey: ["batches"] });
        if (item.batchId !== null) qc.invalidateQueries({ queryKey: ["batch", item.batchId] });
        qc.invalidateQueries({ queryKey: ["status"] });
      })
      .catch((e: unknown) => {
        const aborted = e instanceof ApiError && e.status === -1;
        patch(item.id, { status: aborted ? "cancelled" : "error", error: e instanceof Error ? e.message : "Erreur" });
      })
      .finally(() => {
        controllers.current.delete(item.id);
        started.current.delete(item.id);
      });
  }, [patch, qc]);

  useEffect(() => {
    const active = items.filter((i) => i.status === "uploading").length;
    const pending = items.filter((i) => i.status === "pending" && !started.current.has(i.id));
    pending.slice(0, Math.max(0, CONCURRENCY - active)).forEach(start);
  }, [items, start]);

  const busy = items.some((i) => i.status === "pending" || i.status === "uploading");
  useEffect(() => {
    if (!busy) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); // fermer l'onglet interromprait les envois
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  const enqueue = useCallback((files: File[], options: UploadOptions, batchId: number | null) => {
    const added = files.map((file) => ({
      id: `u${++seq}`, file, name: file.name, size: file.size, batchId, options, status: "pending" as const, sent: 0,
    }));
    setItems((list) => [...list, ...added]);
    return added.map((a) => a.id);
  }, []);

  const cancel = useCallback((id: string) => {
    const c = controllers.current.get(id);
    if (c) c.abort();
    else setItems((list) => list.map((i) => (i.id === id && i.status === "pending" ? { ...i, status: "cancelled" } : i)));
  }, []);

  const retry = useCallback((id: string) => {
    setItems((list) => list.map((i) => (i.id === id && (i.status === "error" || i.status === "cancelled") ? { ...i, status: "pending", sent: 0, error: undefined } : i)));
  }, []);

  const clearFinished = useCallback(() => {
    setItems((list) => list.filter((i) => i.status === "pending" || i.status === "uploading"));
  }, []);

  const value = useMemo(() => ({ items, enqueue, cancel, retry, clearFinished }), [items, enqueue, cancel, retry, clearFinished]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUploads(): UploadState {
  const v = useContext(Ctx);
  if (!v) throw new Error("UploadProvider manquant");
  return v;
}
