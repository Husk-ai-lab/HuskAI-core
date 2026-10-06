import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Connector } from "../api/types";
import { useT } from "../i18n";
import type { ConnectorKind } from "../lib/connectors";

interface Detected { chat_id: string; label?: string | null }

/** Set up one connector. Secrets are typed here, sent once, verified by the provider before they are saved, and
 *  never shown again: after saving the fields are emptied and the server only answers with a hint. */
export function ConnectorForm({ kind, connector, onSaved }: { kind: ConnectorKind; connector?: Connector; onSaved?: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [chat, setChat] = useState<Detected | null>(null);
  const [busy, setBusy] = useState<"" | "detect" | "save" | "test" | "remove">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const configured = connector && connector.state !== "not_configured";

  const run = async (what: typeof busy, fn: () => Promise<string | void>) => {
    setBusy(what);
    setMsg(null);
    try {
      const text = await fn();
      if (text) setMsg({ ok: true, text });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy("");
      qc.invalidateQueries({ queryKey: ["connectors"] });
    }
  };

  const detect = () => run("detect", async () => {
    const r = await api.post<Detected>("/api/v1/connectors/telegram/detect-chat", { token: token.trim() });
    setChat(r);
    return r.label ? t("connectors.chat_found", { label: r.label }) : t("connectors.chat_found_unnamed");
  });
  const save = () => run("save", async () => {
    const body = kind === "telegram"
      ? { token: token.trim(), chat_id: chat?.chat_id, label: chat?.label }
      : { address: address.trim(), app_password: password };
    await api.put(`/api/v1/connectors/${kind}`, body);
    setToken(""); setPassword(""); setAddress(""); setChat(null);  // the secret is not kept in the page
    onSaved?.();
    return t("connectors.saved");
  });
  const test = () => run("test", async () => {
    await api.post(`/api/v1/connectors/${kind}/test`);
    return t(`connectors.test_sent_${kind}`);
  });
  const remove = () => run("remove", async () => { await api.del(`/api/v1/connectors/${kind}`); });

  const canSave = kind === "telegram" ? token.trim().length >= 20 && !!chat : address.includes("@") && password.replace(/\s/g, "").length >= 8;
  return (
    <div>
      {configured && (
        <p className="muted" style={{ marginTop: 0 }}>
          {connector.hint}{connector.target_hint ? ` · ${t("connectors.chat_current", { label: connector.target_hint })}` : ""}
        </p>
      )}
      {kind === "telegram" ? (
        <>
          <div className="field">
            <label htmlFor="tg-token">{t("connectors.label_token")}</label>
            <input id="tg-token" className="input" type="password" autoComplete="off" spellCheck={false} value={token}
                   onChange={(e) => { setToken(e.target.value); setChat(null); }} />
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 14 }}>
            <button type="button" className="btn sm" disabled={token.trim().length < 20 || !!busy} onClick={detect}>
              {busy === "detect" ? t("connectors.detecting") : t("connectors.detect")}
            </button>
            {chat && <span className="ok-badge">{chat.label ? t("connectors.chat_found", { label: chat.label }) : t("connectors.chat_found_unnamed")}</span>}
          </div>
        </>
      ) : (
        <>
          <div className="field">
            <label htmlFor="gm-address">{t("connectors.label_address")}</label>
            <input id="gm-address" className="input" type="email" autoComplete="off" value={address} onChange={(e) => setAddress(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="gm-password">{t("connectors.label_password")}</label>
            <input id="gm-password" className="input" type="password" autoComplete="off" spellCheck={false}
                   placeholder={t("connectors.placeholder_password")} value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
        </>
      )}
      <p className="muted" style={{ fontSize: 12.5 }}>{t("connectors.secret_note")}</p>
      {msg && <div className={`alert ${msg.ok ? "info" : ""}`} role="status" style={{ marginBottom: 12 }}>{msg.text}</div>}
      <div className="actions-row" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        <button type="button" className="btn primary" disabled={!canSave || !!busy} onClick={save}>
          {busy === "save" ? t("connectors.saving") : t("connectors.save")}
        </button>
        {configured && (
          <>
            <button type="button" className="btn" disabled={!!busy} onClick={test}>{busy === "test" ? t("connectors.sending") : t("connectors.test")}</button>
            <button type="button" className="btn ghost" disabled={!!busy} onClick={remove}>{t("connectors.remove")}</button>
          </>
        )}
      </div>
    </div>
  );
}
