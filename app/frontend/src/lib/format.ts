export { fmtDate, fmtDateTime, fmtTime } from "../i18n";

export const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
/** Hours as shown in "~6 h/week" (one decimal at most). */
export const hoursValue = (h: number) => (Number.isInteger(h) ? String(h) : h.toFixed(1));
export const pad2 = (n: number) => String(n).padStart(2, "0");
