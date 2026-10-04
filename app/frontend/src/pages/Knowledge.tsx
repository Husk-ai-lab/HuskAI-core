import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Doc, DocDetail, DocSearchResult, Highlight } from "../api/types";
import { TopBar } from "../components/TopBar";
import { UploadModal } from "../components/UploadModal";
import { useI18n } from "../i18n";
import { fmtDate, fmtSize } from "../lib/format";
import { useDocQuota, useDocuments } from "../lib/hooks";

function highlighted(text: string, hl: Highlight[]): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  [...hl].sort((a, b) => a.start - b.start).forEach((h, i) => {
    if (h.start < last) return;
    out.push(text.slice(last, h.start), <mark key={i}>{text.slice(h.start, h.end)}</mark>);
    last = h.end;
  });
  out.push(text.slice(last));
  return out;
}

function useDebounced<T>(v: T, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export function Knowledge() {
  const [params, setParams] = useSearchParams();
  const { data: docs } = useDocuments();
  const { data: quota } = useDocQuota();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim());
  const [upload, setUpload] = useState(false);
  const qc = useQueryClient();
  const { t, lang } = useI18n();
  const { data: results } = useQuery({
    queryKey: ["doc-search", dq], enabled: dq.length > 1,
    queryFn: () => api.get<DocSearchResult[]>(`/api/v1/documents/search?q=${encodeURIComponent(dq)}`),
  });
  const searching = dq.length > 1;
  const list: { doc: Doc; matches?: number }[] = useMemo(
    () => (searching ? (results ?? []).map((r) => ({ doc: r.document, matches: r.matches })) : (docs ?? []).map((d) => ({ doc: d }))),
    [searching, results, docs],
  );
  const selectedId = params.get("doc") ?? list[0]?.doc.id;
  const { data: detail } = useQuery({
    queryKey: ["doc", selectedId], enabled: !!selectedId,
    queryFn: () => api.get<DocDetail>(`/api/v1/documents/${selectedId}`),
  });
  const hit = searching ? results?.find((r) => r.document.id === selectedId)?.best : undefined;
  const remove = async (id: string) => {
    if (!confirm(t("knowledge.delete_confirm"))) return;
    await api.del(`/api/v1/documents/${id}`);
    setParams({});
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["doc-quota"] });
  };

  return (
    <>
      <TopBar crumb={t("knowledge.crumb")} />
      <div className="content">
        <div className="page wide">
          <div className="eyebrow">{t("knowledge.eyebrow")}</div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 20 }}>
            <div style={{ flex: 1 }}>
              <h1 className="title">{t("knowledge.title")}</h1>
              <p className="lead">{t("knowledge.lead")}</p>
            </div>
            {docs && docs.length > 0 && <button className="btn" style={{ marginBottom: 28 }} onClick={() => setUpload(true)}><Upload size={15} /> {t("knowledge.upload_short")}</button>}
          </div>

          {docs && docs.length === 0 ? (
            <div className="card empty-card">
              <span className="icon-tile lg"><Upload size={22} /></span>
              <h2>{t("knowledge.empty_title")}</h2>
              <p>{t("knowledge.empty_body")}</p>
              <button className="btn primary" onClick={() => setUpload(true)}><Upload size={15} /> {t("upload.title")}</button>
            </div>
          ) : (
            <>
              <div className="card search">
                <Search size={17} className="muted" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("knowledge.search_placeholder")} aria-label={t("knowledge.search")} />
                <span className="mono muted" style={{ fontSize: 11.5 }}>
                  {quota && !searching ? t("knowledge.quota", { count: quota.count, max: quota.limit }) : t("knowledge.document_count", { count: list.length })}
                </span>
              </div>
              <div className="kb-grid">
                <div className="list">
                  {list.map(({ doc, matches }) => (
                    <div key={doc.id} className={`card doc-row ${doc.id === selectedId ? "selected" : ""}`} onClick={() => setParams({ doc: doc.id })}>
                      <span className="doc-kind">{doc.kind}</span>
                      <div style={{ minWidth: 0 }}>
                        <h3>{doc.title}</h3>
                        <div className="file">{doc.source_path || doc.filename}</div>
                      </div>
                      <div className="side">
                        <b>{doc.department || (doc.status === "failed" ? t("common.error") : "—")}</b>
                        {matches !== undefined ? <span className="hit">{t("knowledge.matches", { count: matches })}</span> : <span className="muted">{fmtDate(doc.created_at, lang)}</span>}
                      </div>
                    </div>
                  ))}
                  {searching && list.length === 0 && <p className="muted">{t("knowledge.no_results", { query: dq })}</p>}
                </div>
                {detail && (
                  <div className="card preview">
                    <div style={{ display: "flex", gap: 8 }}>
                      <span className="chip accent">{detail.document.kind}</span>
                      {detail.document.department && <span className="chip">{detail.document.department}</span>}
                      <span style={{ flex: 1 }} />
                      <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => remove(detail.document.id)} title={t("common.delete")}><Trash2 size={13} /></button>
                    </div>
                    <h2>{detail.document.title}</h2>
                    <div className="mono muted" style={{ fontSize: 11.5 }}>{detail.document.source_path || detail.document.filename}</div>
                    <div className="meta">
                      {detail.document.rows != null && <span>{t("knowledge.rows", { count: detail.document.rows })}</span>}
                      <span>{fmtSize(detail.document.size_bytes)}</span>
                      <span>{fmtDate(detail.document.created_at, lang)}</span>
                    </div>
                    {detail.document.status === "failed" && <div className="alert">{t("knowledge.conversion_failed", { detail: detail.document.error ?? "" })}</div>}
                    <div className="label">{t("knowledge.summary")}</div>
                    {detail.card ? (
                      <div className="doc-card">
                        <p className="sentence">{detail.card.sentence}</p>
                        {detail.card.activities.length > 0 && (
                          <ul>{detail.card.activities.map((a) => (
                            <li key={a.id}>{a.what}{a.frequency ? ` · ${a.frequency}` : ""}{a.evidence[0]?.quote ? <q>{a.evidence[0].quote}</q> : null}</li>
                          ))}</ul>
                        )}
                        {detail.card.stats.length > 0 && (
                          <ul className="mono">{detail.card.stats.filter((s) => s.metric !== "sample_row").map((s) => <li key={s.id}>{s.text}</li>)}</ul>
                        )}
                      </div>
                    ) : <span className="muted">{t("knowledge.summary_none")}</span>}
                    <div className="label">{t(hit ? "knowledge.match" : "knowledge.excerpt")}</div>
                    <div className="excerpt">
                      {hit && hit.text ? highlighted(hit.text, hit.highlights) : detail.document.excerpt}
                    </div>
                    {detail.profiles.length > 0 && (
                      <>
                        <div className="label">{t("knowledge.structure")}</div>
                        {detail.profiles.map((p) => (
                          <div key={p.sheet} className="mono" style={{ fontSize: 11.5, marginBottom: 8 }}>
                            <b>{p.sheet}</b> · {t("knowledge.rows", { count: p.n_rows })} · {p.columns.map((c) => c.name + (c.non_null < p.n_rows ? ` (${t("knowledge.empty_cells", { count: p.n_rows - c.non_null })})` : "")).join(", ")}
                          </div>
                        ))}
                      </>
                    )}
                    <div className="label">{t("knowledge.used_by")}</div>
                    {detail.document.used_by?.length ? (
                      detail.document.used_by.map((u) => <div key={u.automation_id}><Link to={`/automazioni/${u.automation_id}`} style={{ color: "var(--accent)", fontWeight: 600 }}>{u.title}</Link></div>)
                    ) : (
                      <span className="muted">{t("knowledge.used_by_none")}</span>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
      {upload && <UploadModal onClose={() => setUpload(false)} />}
    </>
  );
}
