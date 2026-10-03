import { Lightbulb } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useT } from "../i18n";
import { useSuggestionSummary } from "../lib/hooks";
import { SUGGESTIONS_PATH } from "../lib/suggestions";

/** Top-bar light bulb: accent dot when there are unread suggestions, soft pulse while Husk analyses. */
export function SuggestionsButton() {
  const t = useT();
  const { data } = useSuggestionSummary();
  const unread = data?.unread ?? 0;
  const label = unread > 0 ? t("suggestions.button_new", { count: unread }) : t("suggestions.button_title");
  return (
    <NavLink to={SUGGESTIONS_PATH} className={({ isActive }) => `icon-btn bulb ${isActive ? "active" : ""} ${data?.processing ? "busy" : ""}`}
             title={label} aria-label={label}>
      <Lightbulb size={16} />
      {unread > 0 && <span className="dot" data-testid="bulb-dot" />}
    </NavLink>
  );
}
