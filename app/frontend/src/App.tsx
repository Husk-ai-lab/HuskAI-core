import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { api } from "./api/client";
import { ConnectorsWelcome } from "./components/ConnectorsWelcome";
import { MaintenanceAlert } from "./components/MaintenanceAlert";
import { I18nProvider, isLang, useI18n } from "./i18n";
import { useEntitlement, useMe, useSystem } from "./lib/hooks";
import { NotificationsProvider } from "./lib/notifications";
import { SUGGESTIONS_PATH } from "./lib/suggestions";
import { ThemeProvider, useTheme } from "./lib/theme";
import { AuthDone } from "./pages/AuthDone";
import { AutomationEditor } from "./pages/AutomationEditor";
import { Automations } from "./pages/Automations";
import { Discovery } from "./pages/Discovery";
import { Knowledge } from "./pages/Knowledge";
import { Login } from "./pages/Login";
import { MaintenanceScreen } from "./pages/Maintenance";
import { NotEntitled } from "./pages/NotEntitled";
import { Settings } from "./pages/Settings";
import { Suggestions } from "./pages/Suggestions";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false } } });

function Gate() {
  const { data: sys } = useSystem();
  const { data: me, isLoading, isError } = useMe();
  const { data: ent, isLoading: entLoading } = useEntitlement(!!me);
  const { setTheme } = useTheme();
  const { setLang } = useI18n();
  const loc = useLocation();
  useEffect(() => {
    if (me) setTheme(me.settings.theme, false);
  }, [me, setTheme]);
  useEffect(() => {
    // After sign-in the user's saved language wins over browser detection.
    if (me && isLang(me.settings.language)) setLang(me.settings.language);
  }, [me, setLang]);
  useEffect(() => {
    const onUnauth = () => qc.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener("husk:unauthorized", onUnauth);
    return () => window.removeEventListener("husk:unauthorized", onUnauth);
  }, []);

  if (sys?.maintenance.blocking && sys.maintenance.active) return <MaintenanceScreen m={sys.maintenance} />;
  if (isLoading) return <div className="fullscreen"><div className="spinner" /></div>;
  if (isError || !me) {
    if (loc.pathname !== "/accesso") return <Navigate to="/accesso" replace />;
    return <><Login /><MaintenanceAlert /></>;
  }
  if (entLoading) return <div className="fullscreen"><div className="spinner" /></div>;
  if (ent?.allowed === false) {
    const logout = async () => {
      await api.post("/api/v1/user/auth/logout");
      qc.clear();
      window.location.href = "/accesso";
    };
    return <><NotEntitled status={ent.status} onLogout={logout} /><MaintenanceAlert /></>;
  }
  return (
    <>
    <MaintenanceAlert />
    <ConnectorsWelcome />
    <Routes>
      <Route path="/accesso" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/automazioni" replace />} />
        <Route path="/automazioni" element={<Automations />} />
        <Route path="/automazioni/:id" element={<AutomationEditor />} />
        <Route path="/conoscenza" element={<Knowledge />} />
        <Route path="/discovery" element={<Discovery />} />
        <Route path="/impostazioni" element={<Settings />} />
        <Route path={SUGGESTIONS_PATH} element={<Suggestions />} />
        <Route path="*" element={<Navigate to="/automazioni" replace />} />
      </Route>
    </Routes>
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <ThemeProvider>
          <BrowserRouter>
            <NotificationsProvider>
              <Routes>
                <Route path="/auth/done" element={<AuthDone />} />
                <Route path="*" element={<Gate />} />
              </Routes>
            </NotificationsProvider>
          </BrowserRouter>
        </ThemeProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
