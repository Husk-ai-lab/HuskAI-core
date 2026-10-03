import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { Theme } from "../api/types";
import { api } from "../api/client";

interface ThemeCtx { theme: Theme; setTheme: (t: Theme, persist?: boolean) => void; toggle: () => void }
const Ctx = createContext<ThemeCtx>({ theme: "light", setTheme: () => {}, toggle: () => {} });

function read(): Theme {
  const t = document.documentElement.dataset.theme;
  return t === "dark" ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(read);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("husk.theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);
  const setTheme = useCallback((t: Theme, persist = true) => {
    setThemeState(t);
    if (persist) api.patch("/api/v1/user/settings", { theme: t }).catch(() => {});
  }, []);
  const toggle = useCallback(() => setTheme(theme === "dark" ? "light" : "dark"), [theme, setTheme]);
  return <Ctx.Provider value={{ theme, setTheme, toggle }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
