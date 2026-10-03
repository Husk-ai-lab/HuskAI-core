import { useQueryClient } from "@tanstack/react-query";
import { createElement, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Lightbulb } from "lucide-react";
import { useT } from "../i18n";
import { useNotify } from "./notifications";

export const SUGGESTIONS_PATH = "/suggerimenti";

interface CreatedEvent { type: "created"; count: number; top?: { id: string; kind: string; title: string } }

/** One SSE connection per signed-in window: refreshes the bulb dot and shows a snackbar on new suggestions. */
export function useSuggestionEvents() {
  const qc = useQueryClient();
  const notify = useNotify();
  const nav = useNavigate();
  const t = useT();
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/v1/suggestions/events");
    const refresh = () => qc.invalidateQueries({ queryKey: ["suggestions-summary"] });
    es.addEventListener("processing", refresh);
    es.addEventListener("idle", refresh);
    es.addEventListener("created", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as CreatedEvent;
      refresh();
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      notify(t("suggestions.snackbar", { count: d.count }), {
        icon: createElement(Lightbulb, { size: 15 }),
        action: { label: t("suggestions.snackbar_view"), onClick: () => nav(SUGGESTIONS_PATH) },
      });
    });
    return () => es.close();
  }, [qc, notify, nav, t]);
}
