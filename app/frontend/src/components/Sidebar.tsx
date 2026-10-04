import { BookText, ChevronDown, ChevronRight, ArrowRight, Search, SlidersHorizontal, Workflow } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useAutomations, useDiscoveryState, useDocuments, useMe } from "../lib/hooks";
import { automationIcon, docIcon } from "./icons";
import { useT } from "../i18n";
import { Brand } from "./Logo";
import { StudioCard } from "./StudioCard";

function Group({ to, icon, label, children, active }: { to: string; icon: React.ReactNode; label: string; children: React.ReactNode; active: boolean }) {
  const [open, setOpen] = useState(true);
  const t = useT();
  return (
    <div className="nav-group">
      <div className={`nav-item ${active ? "active" : ""}`}>
        <Link to={to} style={{ display: "flex", alignItems: "center", gap: 12, flex: 1 }}>
          {icon}
          <span className="grow">{label}</span>
        </Link>
        <button className="icon-btn" style={{ width: 22, height: 22, border: 0, background: "none" }} onClick={() => setOpen(!open)} aria-label={t(open ? "nav.collapse" : "nav.expand")}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
      </div>
      {open && <div className="nav-sub">{children}</div>}
    </div>
  );
}

export function Sidebar() {
  const loc = useLocation();
  const { data: autos } = useAutomations();
  const { data: docs } = useDocuments();
  const { data: disc } = useDiscoveryState();
  const { data: me } = useMe();
  const t = useT();
  const name = me?.user.display_name || me?.user.email?.split("@")[0] || t("common.user");
  const state = disc?.state ?? "da_fare";
  return (
    <aside className="sidebar">
      <Brand />
      <Group to="/automazioni" icon={<Workflow size={17} />} label={t("nav.automations")} active={loc.pathname.startsWith("/automazioni")}>
        {autos?.length ? (
          autos.slice(0, 3).map((a) => (
            <NavLink key={a.id} to={`/automazioni/${a.id}`} title={a.title}>
              {automationIcon(a, 14)} <span className="t">{a.title}</span>
            </NavLink>
          ))
        ) : (
          <span className="empty">{t("nav.no_automations")}</span>
        )}
        <Link to="/automazioni" className="see-all">{t("nav.see_all_automations")} <ArrowRight size={12} /></Link>
      </Group>
      <Group to="/conoscenza" icon={<BookText size={17} />} label={t("nav.knowledge")} active={loc.pathname.startsWith("/conoscenza")}>
        {docs?.length ? (
          docs.slice(0, 3).map((d) => (
            <Link key={d.id} to={`/conoscenza?doc=${d.id}`} title={d.title} className={loc.search.includes(d.id) ? "active" : ""}>
              {docIcon(d.kind)} <span className="t">{d.title}</span>
            </Link>
          ))
        ) : (
          <span className="empty">{t("nav.no_documents")}</span>
        )}
        <Link to="/conoscenza" className="see-all">{t("nav.see_all")} <ArrowRight size={12} /></Link>
      </Group>
      <NavLink to="/discovery" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
        <Search size={17} />
        <span className="grow">{t("nav.discovery")}</span>
        <span className={`badge-state ${state}`}>{t(`enums.discovery_state.${state}`)}</span>
      </NavLink>
      <div className="sidebar-foot">
        <StudioCard />
        <NavLink to="/impostazioni" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
          <SlidersHorizontal size={17} />
          <span className="grow">{t("nav.settings")}</span>
        </NavLink>
        <div className="user-card">
          <div className="avatar">{name.slice(0, 1).toUpperCase()}</div>
          <div style={{ minWidth: 0 }}>
            <div className="name">{name}</div>
            <div className="sub">{me?.user.org_name || t("nav.workspace")}</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
