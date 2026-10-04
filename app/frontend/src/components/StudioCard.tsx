import { Activity, ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { useState } from "react";
import { useT } from "../i18n";
import { STUDIO_INSTALL_URL, useStudio } from "../lib/studio";

const COLLAPSE_KEY = "husk.studioCard.collapsed";

function readFlag(key: string): boolean {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}
function writeFlag(key: string, on: boolean) {
  try { localStorage.setItem(key, on ? "1" : "0"); } catch { /* storage unavailable: keep it in memory */ }
}

/** Always-visible Husk Studio card in the sidebar: connected → Open, otherwise → Install. Collapsible, never hidden. */
export function StudioCard() {
  const t = useT();
  const st = useStudio();
  const [collapsed, setCollapsed] = useState(() => readFlag(COLLAPSE_KEY));
  const connected = !!st?.alive && !!st.ui_url;
  const href = connected ? st!.ui_url! : st?.install_url || STUDIO_INSTALL_URL;
  const toggle = () => { setCollapsed(!collapsed); writeFlag(COLLAPSE_KEY, !collapsed); };
  return (
    <div className={`studio-card ${connected ? "on" : ""} ${collapsed ? "collapsed" : ""}`}>
      <div className="head">
        <Activity size={14} />
        <b>{t("studio.name")}</b>
        <span className={`studio-dot ${connected ? "on" : ""}`} aria-label={t(connected ? "studio.connected" : "studio.not_running")} />
        <span className="spacer" />
        <button className="icon-btn tiny" onClick={toggle} aria-label={t(collapsed ? "nav.expand" : "nav.collapse")}>
          {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
      </div>
      {!collapsed && (
        <>
          <p>{connected ? t(st?.mcp ? "studio.card_connected_mcp" : "studio.card_connected") : t("studio.card_install")}</p>
          <a className="studio-card-link" href={href} target="_blank" rel="noreferrer">
            {t(connected ? "studio.open" : "studio.install")} <ExternalLink size={11} />
          </a>
        </>
      )}
    </div>
  );
}

const ADVICE_KEY = "husk.studioAdvice.dismissedUntil";
const ADVICE_DAYS = 30;

function adviceHidden(): boolean {
  try { return Number(localStorage.getItem(ADVICE_KEY) || 0) > Date.now(); } catch { return false; }
}

/** Pinned on the Suggestions page: "Husk Studio is recommended for your work". Dismissible for 30 days. */
export function StudioAdviceCard() {
  const t = useT();
  const st = useStudio();
  const [hidden, setHidden] = useState(adviceHidden);
  if (hidden) return null;
  const connected = !!st?.alive && !!st.ui_url;
  const dismiss = () => {
    try { localStorage.setItem(ADVICE_KEY, String(Date.now() + ADVICE_DAYS * 86_400_000)); } catch { /* in memory only */ }
    setHidden(true);
  };
  return (
    <div className="card studio-advice">
      <span className="icon-tile"><Activity size={18} /></span>
      <div style={{ minWidth: 0 }}>
        <div className="row-title"><span className="chip accent">{t("studio.name")}</span></div>
        <h3>{t(connected ? "suggestions.studio_card_title_on" : "suggestions.studio_card_title")}</h3>
        <p className="row-desc">{t(connected ? "suggestions.studio_card_body_on" : "suggestions.studio_card_body")}</p>
      </div>
      <div className="sugg-actions">
        <a className="btn sm primary" href={connected ? st!.ui_url! : st?.install_url || STUDIO_INSTALL_URL} target="_blank" rel="noreferrer">
          {t(connected ? "studio.open" : "studio.install")} <ExternalLink size={12} />
        </a>
        <button className="btn sm ghost" onClick={dismiss}>{t("suggestions.studio_card_dismiss")}</button>
      </div>
    </div>
  );
}
