import { LANGUAGE_LABELS, SUPPORTED, useI18n, type Lang } from "../i18n";

/** Segmented language picker. `onChange` lets callers persist the choice (e.g. user settings). */
export function LanguageSwitch({ onChange, compact = false }: { onChange?: (l: Lang) => void; compact?: boolean }) {
  const { lang, setLang, t } = useI18n();
  return (
    <div className="segmented" role="radiogroup" aria-label={t("common.language")} style={compact ? { padding: 2 } : undefined}>
      {SUPPORTED.map((l) => (
        <button
          key={l}
          type="button"
          role="radio"
          aria-checked={lang === l}
          className={lang === l ? "on" : ""}
          style={compact ? { padding: "5px 10px", fontSize: 12.5 } : undefined}
          onClick={() => {
            setLang(l);
            onChange?.(l);
          }}
        >
          {compact ? l.toUpperCase() : LANGUAGE_LABELS[l]}
        </button>
      ))}
    </div>
  );
}
