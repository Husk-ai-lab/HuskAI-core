import { ArrowRight, ChevronRight, Search, Upload } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { automationIcon } from "../components/icons";
import { StudioAction } from "../components/StudioAction";
import { TopBar } from "../components/TopBar";
import { UploadModal } from "../components/UploadModal";
import { useT } from "../i18n";
import { hoursValue, pad2 } from "../lib/format";
import { useAutomations, useDiscoveryState, useDocuments } from "../lib/hooks";

export function Automations() {
  const { data: autos, isLoading } = useAutomations();
  const { data: docs } = useDocuments();
  const { data: disc } = useDiscoveryState();
  const [tab, setTab] = useState<"tutte" | "automazioni">("tutte");
  const [upload, setUpload] = useState(false);
  const nav = useNavigate();
  const t = useT();
  const docName = (id: string) => docs?.find((d) => d.id === id)?.filename ?? id;
  const ready = docs?.filter((d) => d.status === "ingested").length ?? 0;

  return (
    <>
      <TopBar crumb={t("automations.crumb")} />
      <div className="content">
        <div className="page wide">
          <div className="eyebrow">{t("automations.eyebrow")}</div>
          <h1 className="title">{t("automations.title")}</h1>
          <p className="lead">{t("automations.lead")}</p>

          {isLoading ? null : autos && autos.length > 0 ? (
            <>
              <div className="tabs">
                <button className={`tab ${tab === "tutte" ? "active" : ""}`} onClick={() => setTab("tutte")}>{t("automations.tab_all")}<sup>{autos.length}</sup></button>
                <button className={`tab ${tab === "automazioni" ? "active" : ""}`} onClick={() => setTab("automazioni")}>{t("automations.tab_automations")}<sup>{autos.length}</sup></button>
              </div>
              <div className="list">
                {autos.map((a, i) => (
                  <div key={a.id} className="card row-card" onClick={() => nav(`/automazioni/${a.id}`)}>
                    <span className="row-num">{pad2(i + 1)}</span>
                    <span className="icon-tile">{automationIcon(a)}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="row-title"><h3>{a.title}</h3><span className="chip">{t("common.automation_chip")}</span></div>
                      <p className="row-desc">{a.description}</p>
                      <div className="row-meta">
                        <span>{t("common.source", { source: a.source_document_ids.slice(0, 1).map(docName).join("") + (a.source_document_ids.length > 1 ? ` +${a.source_document_ids.length - 1}` : "") })}</span>
                        <span>{t("common.estimated_impact", { hours: hoursValue(a.hours_per_week) })}</span>
                        {a.status === "errore" && <StudioAction />}
                      </div>
                    </div>
                    <span className={`status-dot ${a.status}`}>{t(`enums.automation_status.${a.status}`)}</span>
                    <ChevronRight size={16} className="muted" />
                  </div>
                ))}
              </div>
            </>
          ) : ready === 0 ? (
            <div className="card empty-card">
              <span className="icon-tile lg"><Upload size={22} /></span>
              <h2>{t("automations.empty_no_docs_title")}</h2>
              <p>{t("automations.empty_no_docs_body")}</p>
              <button className="btn primary" onClick={() => setUpload(true)}><Upload size={15} /> {t("upload.title")}</button>
            </div>
          ) : (
            <div className="card empty-card">
              <span className="icon-tile lg"><Search size={22} /></span>
              <h2>{t("automations.empty_ready_title", { count: ready })}</h2>
              <p>{t("automations.empty_ready_body")}</p>
              <button className="btn primary" onClick={() => nav("/discovery")}>
                {t(disc?.state === "in_corso" ? "automations.follow_discovery" : "automations.go_discovery")} <ArrowRight size={15} />
              </button>
            </div>
          )}
        </div>
      </div>
      {upload && <UploadModal onClose={() => setUpload(false)} />}
    </>
  );
}
