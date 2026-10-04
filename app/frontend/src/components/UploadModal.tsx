import { useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Doc } from "../api/types";
import { useT } from "../i18n";
import { ACCEPT, chunks, classify, fromDataTransfer, fromFileList, groupByFolder, type Picked } from "../lib/folderUpload";
import { useDocQuota } from "../lib/hooks";
import { useNotify } from "../lib/notifications";

interface UploadResponse { documents: Doc[]; rejected: { filename: string; reason: string }[] }

export function UploadModal({ onClose }: { onClose: () => void }) {
  const [files, setFiles] = useState<Picked[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [overLimit, setOverLimit] = useState(0);
  const { data: quota } = useDocQuota();
  const remaining = quota ? Math.max(0, quota.limit - quota.count) : Infinity;
  const [authorized, setAuthorized] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const notify = useNotify();
  const t = useT();

  useEffect(() => {
    // not in React's typings; supported by every desktop browser engine
    folderInput.current?.setAttribute("webkitdirectory", "");
    folderInput.current?.setAttribute("directory", "");
  }, []);

  const add = (picked: Picked[]) => {
    const { ok, skipped: n } = classify(picked);
    setFiles((cur) => {
      const seen = new Set(cur.map((p) => p.path));
      const fresh = ok.filter((p) => !seen.has(p.path));
      const room = Math.max(0, remaining - cur.length);  // the server enforces the cap too
      if (fresh.length > room) setOverLimit((o) => o + fresh.length - room);
      return [...cur, ...fresh.slice(0, room)];
    });
    setSkipped((s) => s + n);
  };
  const busy = progress !== null;

  const submit = async () => {
    setError(null);
    const errors: string[] = [];
    const batches = chunks(files);
    setProgress({ done: 0, total: files.length });
    let done = 0;
    for (const batch of batches) {
      const fd = new FormData();
      batch.forEach((p) => { fd.append("files", p.file); fd.append("paths", p.path); });
      fd.append("authorized", String(authorized));
      try {
        const res = await api.form<UploadResponse>("/api/v1/documents", fd);
        errors.push(...res.rejected.map((r) => `${r.filename}: ${r.reason}`));
      } catch (e) {
        errors.push((e as Error).message);
      }
      done += batch.length;
      setProgress({ done, total: files.length });
    }
    await qc.invalidateQueries({ queryKey: ["documents"] });
    await qc.invalidateQueries({ queryKey: ["discovery-state"] });
    await qc.invalidateQueries({ queryKey: ["suggestions-summary"] });
    await qc.invalidateQueries({ queryKey: ["doc-quota"] });
    setProgress(null);
    if (errors.length) setError(errors.join(" · "));
    else {
      notify(t("upload.done_suggestions"));
      onClose();
    }
  };

  const groups = groupByFolder(files);
  return (
    <div className="overlay" onClick={busy ? undefined : onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal>
        <h2>{t("upload.title")}</h2>
        <p className="muted" style={{ margin: 0 }}>{t("upload.subtitle")}</p>
        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={async (e) => { e.preventDefault(); setDrag(false); add(await fromDataTransfer(e.dataTransfer)); }}
        >
          <Upload size={22} />
          <div style={{ marginTop: 8 }}>{t("upload.drop")} <b>{t("upload.browse")}</b></div>
          <div className="mono" style={{ fontSize: 11, marginTop: 6 }}>PDF · DOCX · XLSX · CSV · PPTX · MD · DB</div>
          <input ref={input} type="file" multiple hidden accept={ACCEPT} data-testid="file-input"
                 onChange={(e) => { add(fromFileList(e.target.files ?? [])); e.target.value = ""; }} />
        </div>
        <div className="picker-row">
          <button className="btn sm" onClick={() => input.current?.click()} disabled={busy}><Upload size={13} /> {t("upload.choose_files")}</button>
          <button className="btn sm" onClick={() => folderInput.current?.click()} disabled={busy}><FolderOpen size={13} /> {t("upload.choose_folder")}</button>
          <input ref={folderInput} type="file" multiple hidden data-testid="folder-input"
                 onChange={(e) => { add(fromFileList(e.target.files ?? [])); e.target.value = ""; }} />
          {files.length > 0 && !busy && (
            <button className="btn sm ghost" onClick={() => { setFiles([]); setSkipped(0); setOverLimit(0); }}>{t("upload.clear")}</button>
          )}
        </div>
        {(files.length > 0 || skipped > 0) && (
          <div className="file-summary">
            <b>{t("upload.files_summary", { count: files.length })}</b>
            {skipped > 0 && <span className="muted"> · {t("upload.skipped", { count: skipped })}</span>}
            <ul className="file-list">
              {groups.length > 1 || groups[0]?.[0]
                ? groups.slice(0, 12).map(([folder, n]) => <li key={folder}><FolderOpen size={11} /> {folder || t("upload.folder_root")} · {n}</li>)
                : files.slice(0, 12).map((p) => <li key={p.path}>{p.file.name}</li>)}
            </ul>
          </div>
        )}
        {quota && <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>{t("upload.limit_left", { count: Math.max(0, remaining - files.length), max: quota.limit })}</div>}
        {overLimit > 0 && quota && <div className="alert" style={{ marginBottom: 12 }}>{t("upload.limit_over", { count: overLimit, max: quota.limit })}</div>}
        <label className="check">
          <input type="checkbox" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
          <span>{t("upload.authorize")}</span>
        </label>
        {progress && (
          <div style={{ marginTop: 14 }}>
            <div className="bar"><i style={{ width: `${Math.max(4, (progress.done / Math.max(1, progress.total)) * 100)}%` }} /></div>
            <div className="mono muted" style={{ fontSize: 11.5, marginTop: 6 }}>{t("upload.progress", { done: progress.done, total: progress.total })}</div>
          </div>
        )}
        {error && <div className="alert" style={{ marginTop: 14 }}>{error}</div>}
        <div className="actions-row">
          <button className="btn" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button className="btn primary" disabled={!files.length || !authorized || busy} onClick={submit}>
            {busy ? t("upload.uploading") : t("upload.submit", { count: files.length })}
          </button>
        </div>
      </div>
    </div>
  );
}
