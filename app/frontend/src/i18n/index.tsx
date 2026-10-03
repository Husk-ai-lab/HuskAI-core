/**
 * i18n — the frontend reads the SAME catalogues as the backend: app/i18n/locales/<lang>.json.
 * Keys are flat and dot-namespaced; "{name}" placeholders; plurals via "key_one"/"key_other"
 * with a `count` param (Intl.PluralRules). Fallback: language → en → key.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import en from "@i18n/en.json";
import it from "@i18n/it.json";

const CATALOGUES: Record<string, Record<string, string>> = { en, it };
export type Lang = "en" | "it";
export const SUPPORTED: Lang[] = ["en", "it"];
export const DEFAULT_LANG: Lang = "en";
export const LANGUAGE_LABELS: Record<Lang, string> = { en: "English", it: "Italiano" };
const LOCALES: Record<Lang, string> = { en: "en-GB", it: "it-IT" };
const STORAGE_KEY = "husk.lang";

type Params = Record<string, string | number | null | undefined>;

let current: Lang = DEFAULT_LANG;
/** Current UI language for non-React code (API client headers). */
export const currentLang = () => current;
export const locale = (lang: Lang = current) => LOCALES[lang];

export function isLang(v: unknown): v is Lang {
  return typeof v === "string" && (SUPPORTED as string[]).includes(v);
}

/** Before login: saved choice → browser language (Italian if the computer is Italian) → English. */
export function detectLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
  } catch {
    /* storage unavailable */
  }
  const prefs = typeof navigator !== "undefined" ? navigator.languages ?? [navigator.language] : [];
  for (const p of prefs) {
    const code = (p || "").toLowerCase().split("-")[0];
    if (isLang(code)) return code;
  }
  return DEFAULT_LANG;
}

function lookup(lang: Lang, key: string): string | undefined {
  return CATALOGUES[lang]?.[key] ?? CATALOGUES[DEFAULT_LANG]?.[key];
}

export function translate(lang: Lang, key: string, params: Params = {}): string {
  let template: string | undefined;
  if (params.count !== undefined && params.count !== null) {
    const rule = new Intl.PluralRules(LOCALES[lang]).select(Number(params.count));
    template = lookup(lang, `${key}_${rule}`) ?? lookup(lang, `${key}_other`);
  }
  template = template ?? lookup(lang, key);
  if (template === undefined) return key;
  return template.replace(/\{(\w+)\}/g, (m, name) => (params[name] === undefined || params[name] === null ? m : String(params[name])));
}

export function fmtDate(iso?: string | null, lang: Lang = current) {
  return iso ? new Date(iso).toLocaleDateString(LOCALES[lang], { day: "numeric", month: "short", year: "numeric" }) : "";
}
export function fmtDateTime(iso?: string | null, lang: Lang = current) {
  return iso ? new Date(iso).toLocaleString(LOCALES[lang], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}
export function fmtTime(iso?: string | null, lang: Lang = current) {
  return iso ? new Date(iso).toLocaleTimeString(LOCALES[lang], { hour: "2-digit", minute: "2-digit" }) : "";
}

interface I18nCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, params?: Params) => string;
}

const Ctx = createContext<I18nCtx>({ lang: DEFAULT_LANG, setLang: () => {}, t: (k, p) => translate(DEFAULT_LANG, k, p) });

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Lang }) {
  const [lang, setLangState] = useState<Lang>(() => {
    current = initial ?? detectLang();
    if (typeof document !== "undefined") document.documentElement.lang = current;
    return current;
  });
  const setLang = useCallback((l: Lang) => {
    current = l;
    if (typeof document !== "undefined") document.documentElement.lang = l;
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* storage unavailable */
    }
    setLangState(l);
  }, []);
  const value = useMemo(() => ({ lang, setLang, t: (key: string, params?: Params) => translate(lang, key, params) }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
export const useT = () => useContext(Ctx).t;
