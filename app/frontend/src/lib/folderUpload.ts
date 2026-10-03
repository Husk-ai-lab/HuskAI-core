/** Folder uploads: walk dropped folders, keep relative paths, skip system and unsupported files. */

export const ACCEPT = ".pdf,.docx,.xlsx,.xlsm,.xls,.csv,.tsv,.pptx,.md,.txt,.json,.db,.sqlite,.sqlite3";
const EXTENSIONS = new Set(ACCEPT.split(","));
const SYSTEM_FILE = /^(\.|~\$)|^(thumbs\.db|desktop\.ini|icon\r?)$/i;
export const CHUNK_SIZE = 15;

export interface Picked { file: File; path: string }

export function isSupported(name: string): boolean {
  if (SYSTEM_FILE.test(name)) return false;
  const dot = name.lastIndexOf(".");
  return dot > 0 && EXTENSIONS.has(name.slice(dot).toLowerCase());
}

/** Split into supported files and the number skipped (system files, hidden folders, unsupported formats). */
export function classify(items: Picked[]): { ok: Picked[]; skipped: number } {
  const ok = items.filter((p) => isSupported(p.file.name) && !p.path.split("/").slice(0, -1).some((seg) => seg.startsWith(".")));
  return { ok, skipped: items.length - ok.length };
}

export const fromFileList = (files: FileList | File[]): Picked[] =>
  Array.from(files).map((file) => ({ file, path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name }));

export function folderOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}

/** Group by folder for the file list ("acquisti/ordini" → 3). */
export function groupByFolder(items: Picked[]): [string, number][] {
  const m = new Map<string, number>();
  for (const p of items) m.set(folderOf(p.path), (m.get(folderOf(p.path)) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export function chunks<T>(items: T[], size = CHUNK_SIZE): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

// --- drag & drop of folders (FileSystem Entry API; available in every Chromium/WebKit/Gecko build) ---
export interface EntryLike {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (ok: (f: File) => void, err?: (e: unknown) => void) => void;
  createReader?: () => { readEntries: (ok: (entries: EntryLike[]) => void, err?: (e: unknown) => void) => void };
}

async function readAll(dir: EntryLike): Promise<EntryLike[]> {
  const reader = dir.createReader!();
  const all: EntryLike[] = [];
  for (;;) {
    const batch = await new Promise<EntryLike[]>((ok, err) => reader.readEntries(ok, err));
    if (!batch.length) return all;  // readEntries returns at most ~100 entries per call
    all.push(...batch);
  }
}

export async function walkEntry(entry: EntryLike, prefix = ""): Promise<Picked[]> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile && entry.file) {
    const file = await new Promise<File>((ok, err) => entry.file!(ok, err));
    return [{ file, path }];
  }
  if (entry.isDirectory && entry.createReader) {
    const children = await readAll(entry);
    return (await Promise.all(children.map((c) => walkEntry(c, path)))).flat();
  }
  return [];
}

export async function fromDataTransfer(dt: DataTransfer): Promise<Picked[]> {
  const entries: EntryLike[] = [];
  for (const it of Array.from(dt.items ?? [])) {
    const entry = it.webkitGetAsEntry?.() as unknown as EntryLike | null | undefined;
    if (entry) entries.push(entry);
  }
  if (!entries.length) return fromFileList(dt.files);
  return (await Promise.all(entries.map((e) => walkEntry(e)))).flat();
}
