export const CHANNELS = ["desktop", "telegram", "gmail"] as const;
export type Channel = (typeof CHANNELS)[number];

const ADDRESS = /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/;
export const MAX_RECIPIENTS = 10;

/** What the user typed in "To": addresses split on commas, semicolons or spaces, each marked valid or not (the server
 *  checks them again; this is only so the popup can say so while typing). */
export function parseRecipients(text: string): { value: string; valid: boolean }[] {
  const seen = new Set<string>();
  return text.split(/[,;\s]+/).map((v) => v.trim()).filter((v) => v && !seen.has(v) && !!seen.add(v)).map((value) => ({ value, valid: ADDRESS.test(value) }));
}

export const recipientsOk = (text: string) => {
  const r = parseRecipients(text);
  return r.every((x) => x.valid) && r.length <= MAX_RECIPIENTS;
};
