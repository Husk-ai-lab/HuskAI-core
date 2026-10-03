import { useEffect, useState } from "react";
import type { Maintenance, MaintenanceWindow } from "../api/types";
import { locale, translate, type Lang } from "../i18n";

/** Windows announced this far ahead get the startup modal. */
export const ALERT_AHEAD_H = 24;
/** In the last hour before a window the banner counts down. */
export const COUNTDOWN_MIN = 60;
/** Warn before starting long AI work this close to a blocking window. */
export const WORK_WARNING_MIN = 30;

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export const minutesUntil = (iso: string, now = Date.now()) => Math.max(0, Math.ceil((new Date(iso).getTime() - now) / 60_000));

export function fmtWindow(w: { starts_at: string; ends_at: string }, lang: Lang): string {
  const s = new Date(w.starts_at);
  const e = new Date(w.ends_at);
  const loc = locale(lang);
  const day = s.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" });
  const time = (d: Date) => d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  const sameDay = s.toDateString() === e.toDateString();
  const text = sameDay
    ? translate(lang, "maintenance.window_same_day", { day, start: time(s), end: time(e) })
    : translate(lang, "maintenance.window_multi_day", { day, start: time(s), end: time(e), endDay: e.toLocaleDateString(loc) });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Neon ships message_<lang> columns; future languages fall back to English. */
export const message = (w: Pick<MaintenanceWindow, "message_en" | "message_it">, lang: Lang) =>
  (w as unknown as Record<string, string | undefined>)[`message_${lang}`] || w.message_en || w.message_it;

/** Upcoming windows that haven't started yet, soonest first (the API already sorts them). */
export function upcoming(m: Maintenance | undefined, now = Date.now()): MaintenanceWindow[] {
  return (m?.upcoming ?? []).filter((w) => new Date(w.starts_at).getTime() > now);
}

export function nextBlocking(m: Maintenance | undefined, now = Date.now()): MaintenanceWindow | undefined {
  return upcoming(m, now).find((w) => w.blocking);
}

const ACK_KEY = "husk.maintenance.ack";

export function acknowledged(): string[] {
  try {
    return JSON.parse(localStorage.getItem(ACK_KEY) ?? "[]");
  } catch {
    return [];
  }
}

export function acknowledge(id: string): void {
  try {
    localStorage.setItem(ACK_KEY, JSON.stringify([...acknowledged().slice(-40), id]));
  } catch {
    /* private mode: the modal may show again next time, which is acceptable */
  }
}
