import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { useT } from "../i18n";

/** The AI key input (masked, with show/hide), shared by the first-access screen and Settings. */
export function AiKeyField({ value, onChange, id = "ai-key", autoFocus = false, placeholder, compact = false }: {
  value: string; onChange: (v: string) => void; id?: string; autoFocus?: boolean; placeholder?: string; compact?: boolean;
}) {
  const t = useT();
  const [show, setShow] = useState(false);
  return (
    <div style={{ position: "relative", width: compact ? 240 : undefined }}>
      <input id={id} className="input mono" type={show ? "text" : "password"} autoComplete="off" spellCheck={false}
             placeholder={placeholder ?? t("aiKey.key_placeholder")} value={value} onChange={(e) => onChange(e.target.value)}
             autoFocus={autoFocus} style={{ paddingRight: 44, height: compact ? 38 : undefined }} />
      <button type="button" className="icon-btn" onClick={() => setShow(!show)} aria-label={t(show ? "aiKey.hide" : "aiKey.show")}
              style={{ position: "absolute", right: 4, top: compact ? 2 : 5, width: 34, height: 34, border: 0 }}>
        {show ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );
}

export const AI_KEY_MIN = 8;
