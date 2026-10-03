import { Info } from "lucide-react";
import { Outlet } from "react-router-dom";
import { useT } from "../i18n";
import { useSystem } from "../lib/hooks";
import { useSuggestionEvents } from "../lib/suggestions";
import { MaintenanceBanner } from "./MaintenanceAlert";
import { Sidebar } from "./Sidebar";

export function Banners() {
  const { data } = useSystem();
  const t = useT();
  if (!data) return null;
  return (
    <>
      <MaintenanceBanner />
      {data.release.update_available && (
        <div className="banner"><Info size={15} /> {t("common.update_available", { version: data.release.latest ?? "" })}</div>
      )}
    </>
  );
}

export function Layout() {
  useSuggestionEvents();
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Banners />
        <Outlet />
      </main>
    </div>
  );
}
