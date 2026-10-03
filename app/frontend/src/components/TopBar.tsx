import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useT } from "../i18n";
import { useTheme } from "../lib/theme";
import { SuggestionsButton } from "./SuggestionsButton";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const t = useT();
  return (
    <button className="icon-btn" onClick={toggle} title={t(theme === "dark" ? "common.theme_light" : "common.theme_dark")} aria-label={t("common.toggle_theme")}>
      {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

/** Right side of every signed-in top bar: suggestions, then theme. */
export function TopActions() {
  return (
    <>
      <SuggestionsButton />
      <ThemeToggle />
    </>
  );
}

export function TopBar({ crumb, children }: { crumb: string; children?: ReactNode }) {
  return (
    <header className="topbar">
      {children}
      <span className="crumb">{crumb}</span>
      <span className="spacer" />
      <TopActions />
    </header>
  );
}
