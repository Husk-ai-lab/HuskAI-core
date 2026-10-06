import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Automation, AutomationView, FlowNode } from "../api/types";
import { useT } from "../i18n";
import { useConnectors, type ConnectorKind } from "../lib/connectors";
import { useMe } from "../lib/hooks";
import { CHANNELS, MAX_RECIPIENTS, parseRecipients, recipientsOk, type Channel } from "../lib/notification";
import { ConnectorModal } from "./ConnectorModal";
import { ConnectorStatus } from "./ConnectorStatus";
import { channelIcon } from "./icons";

/** Choose how a notification node is delivered: the channel (this computer, Telegram, Gmail) and, for email, who gets
 *  it. The user's own decision, so it is applied at once (a new version: Undo works), not as a proposal. */
export function NotificationPopup({ automation, node, onClose }: { automation: Automation; node: FlowNode; onClose: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const { data: connectors } = useConnectors();
  const [channel, setChannel] = useState<Channel>(node.channel ?? "desktop");
  const [recipients, setRecipients] = useState(node.recipients ?? "");
  const [subject, setSubject] = useState(node.subject ?? "");
  const [template, setTemplate] = useState(node.template ?? "");
  const [attach, setAttach] = useState(!!node.attach_report);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [setup, setSetup] = useState<ConnectorKind | null>(null);

  const parsed = parseRecipients(recipients);
  const canSave = channel !== "gmail" || recipientsOk(recipients);
  const own = me?.user.email;
  const addOwn = () => own && setRecipients((r) => (parseRecipients(r).some((x) => x.value === own) ? r : [r.trim(), own].filter(Boolean).join(", ")));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.put<Automation>(`/api/v1/automations/${automation.id}/nodes/${node.id}/notification`, {
        channel, recipients: channel === "gmail" ? recipients : null, subject: subject || null, template: template || null, attach_report: channel === "desktop" ? false : attach,
      });
      qc.setQueryData<AutomationView>(["automation", automation.id], (old) => (old ? { ...old, automation: updated } : old));
      qc.invalidateQueries({ queryKey: ["automation", automation.id] });
      qc.invalidateQueries({ queryKey: ["automations"] });
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <>
      <div className="overlay" onClick={onClose}>
        <div className="modal card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal aria-label={t("notification.popup_title")}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <h2 style={{ flex: 1 }}>{t("notification.popup_title")}</h2>
            <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><X size={15} /></button>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>{t("notification.popup_subtitle", { label: node.label })}</p>

          <div className="field">
            <label id="notif-channel">{t("notification.section_channel")}</label>
            <div role="radiogroup" aria-labelledby="notif-channel" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {CHANNELS.map((c) => {
                const conn = c === "desktop" ? undefined : connectors?.find((x) => x.kind === c);
                const ready = c === "desktop" || conn?.state === "ready";
                return (
                  <div key={c} role="radio" aria-checked={channel === c} tabIndex={0} className={`channel-card ${channel === c ? "on" : ""}`}
                       onClick={() => setChannel(c)} onKeyDown={(e) => (e.key === " " || e.key === "Enter") && setChannel(c)}>
                    <span className="icon-tile">{channelIcon(c, 18)}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <b>{t(`notification.channel_${c}`)}</b>
                      <div className="muted" style={{ fontSize: 12.5 }}>{c === "desktop" ? t("notification.channel_hint_desktop") : conn?.hint ?? t(`connectors.desc_${c}`)}</div>
                    </div>
                    {c === "desktop" ? <span className="status-dot attiva">{t("connectors.status_ready")}</span> : <ConnectorStatus connector={conn} />}
                    {!ready && <button type="button" className="btn sm" onClick={(e) => { e.stopPropagation(); setSetup(c as ConnectorKind); }}>{t("connectors.setup")}</button>}
                  </div>
                );
              })}
            </div>
          </div>

          {channel === "gmail" && (
            <div className="field">
              <label htmlFor="notif-to">{t("notification.recipients")}</label>
              <input id="notif-to" className="input" type="text" autoComplete="off" spellCheck={false} placeholder={t("notification.recipients_placeholder")}
                     value={recipients} onChange={(e) => setRecipients(e.target.value)} />
              {parsed.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {parsed.map((r) => <span key={r.value} className={`chip ${r.valid ? "accent" : "bad"}`} title={r.valid ? undefined : t("notification.recipient_invalid", { value: r.value })}>{r.value}</span>)}
                </div>
              )}
              <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
                {t("notification.recipients_hint", { max: MAX_RECIPIENTS })}{" "}
                {own && <button type="button" className="btn sm ghost" onClick={addOwn}>{t("notification.add_mine", { email: own })}</button>}
              </div>
            </div>
          )}
          {channel === "gmail" && (
            <div className="field">
              <label htmlFor="notif-subject">{t("notification.subject")}</label>
              <input id="notif-subject" className="input" type="text" value={subject} onChange={(e) => setSubject(e.target.value)} />
            </div>
          )}
          <div className="field">
            <label htmlFor="notif-message">{t("notification.message")}</label>
            <textarea id="notif-message" className="input" rows={3} value={template} onChange={(e) => setTemplate(e.target.value)} />
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{t("notification.message_hint")}</div>
          </div>
          {channel !== "desktop" && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}>
              <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} /> {t("notification.attach_report")}
            </label>
          )}
          {error && <div className="alert" style={{ marginTop: 12 }}>{error}</div>}
          <div className="actions-row">
            <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="btn primary" disabled={busy || !canSave} onClick={save}>{busy ? t("notification.saving") : t("notification.save")}</button>
          </div>
        </div>
      </div>
      {setup && <ConnectorModal kind={setup} initialTab="setup" onClose={() => setSetup(null)} />}
    </>
  );
}
