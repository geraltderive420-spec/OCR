import { DragEvent, useEffect, useRef, useState } from "react";
import { fr, t } from "../i18n/fr";
import { Icon } from "./Icon";

// Lecture récursive d'un dossier glissé (API « entries » des navigateurs Chromium / Firefox / Edge).
async function readEntry(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.isFile) {
    out.push(await new Promise<File>((ok, ko) => (entry as FileSystemFileEntry).file(ok, ko)));
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) { // readEntries rend les fichiers par paquets de 100
      const batch = await new Promise<FileSystemEntry[]>((ok, ko) => reader.readEntries(ok, ko));
      if (!batch.length) break;
      for (const e of batch) await readEntry(e, out);
    }
  }
}

async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const entries = Array.from(dt.items ?? [])
    .map((it) => it.webkitGetAsEntry?.() ?? null)
    .filter((e): e is FileSystemEntry => e !== null);
  if (!entries.length) return Array.from(dt.files);
  const out: File[] = [];
  for (const e of entries) {
    try {
      await readEntry(e, out);
    } catch {
      /* élément illisible : ignoré */
    }
  }
  return out;
}

export default function Dropzone({ onFiles, accept, maxMb, maxPages, compact }: {
  onFiles: (files: File[]) => void; accept: string[]; maxMb: number; maxPages: number; compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  useEffect(() => {
    folder.current?.setAttribute("webkitdirectory", "");
    folder.current?.setAttribute("directory", "");
  }, []);

  const drop = async (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    onFiles(await filesFromDrop(e.dataTransfer));
  };

  return (
    <div
      className={`dropzone ${over ? "over" : ""}`}
      style={compact ? { padding: "1.2rem" } : undefined}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setOver(false); }}
      onDrop={drop}
      onClick={() => files.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && files.current?.click()}
      aria-label={fr.newDoc.chooseFiles}
    >
      {!compact && <span className="big-ico"><Icon name="upload" /></span>}
      <h3>{fr.newDoc.dropTitle}</h3>
      <div className="row" style={{ justifyContent: "center" }} onClick={(e) => e.stopPropagation()}>
        <button type="button" className="primary" onClick={() => files.current?.click()}><Icon name="files" />{fr.newDoc.chooseFiles}</button>
        <button type="button" onClick={() => folder.current?.click()}><Icon name="folder" />{fr.newDoc.chooseFolder}</button>
      </div>
      <p className="muted small">{t(fr.newDoc.limits, { mb: maxMb, pages: maxPages })}</p>
      <input ref={files} type="file" multiple hidden accept={accept.join(",")}
        onChange={(e) => { onFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      <input ref={folder} type="file" multiple hidden
        onChange={(e) => { onFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
    </div>
  );
}
