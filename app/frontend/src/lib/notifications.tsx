import { X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { StudioAction } from "../components/StudioAction";
import { useT } from "../i18n";

/** Temporary snackbars (bottom centre). One provider for the whole app; `useNotify()` to show one. */
export interface SnackAction { label: string; onClick: () => void }
/** `studio`: the action blocked on an error → show "Show in Husk Studio" / "Install Husk Studio". */
export interface SnackOptions { action?: SnackAction; icon?: ReactNode; duration?: number; studio?: { traceId?: string | null; traced?: boolean } }
interface Snack extends SnackOptions { id: number; text: string }

type Notify = (text: string, options?: SnackOptions) => void;
const Ctx = createContext<Notify>(() => undefined);

export const SNACK_MS = 6000;
const MAX_VISIBLE = 3;

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [snacks, setSnacks] = useState<Snack[]>([]);
  const next = useRef(1);
  const t = useT();
  const close = useCallback((id: number) => setSnacks((all) => all.filter((s) => s.id !== id)), []);
  const notify = useCallback<Notify>((text, options = {}) => {
    const id = next.current++;
    setSnacks((all) => [...all, { id, text, ...options }].slice(-MAX_VISIBLE));
    setTimeout(() => close(id), options.duration ?? SNACK_MS);
  }, [close]);
  const value = useMemo(() => notify, [notify]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="snackbars" role="status" aria-live="polite">
        {snacks.map((s) => (
          <div key={s.id} className="snackbar">
            {s.icon}
            <span className="text">{s.text}</span>
            {s.studio && <StudioAction traceId={s.studio.traceId} traced={s.studio.traced} />}
            {s.action && (
              <button className="snack-action" onClick={() => { s.action!.onClick(); close(s.id); }}>{s.action.label}</button>
            )}
            <button className="snack-close" onClick={() => close(s.id)} aria-label={t("common.close")}><X size={13} /></button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useNotify = () => useContext(Ctx);
