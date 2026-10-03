/** Tiny fetch wrapper: same-origin, cookie session; errors come back translated (Accept-Language). */
import { currentLang } from "../i18n";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: { "Accept-Language": currentLang() } };
  if (body !== undefined) {
    if (isForm) init.body = body as FormData;
    else {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      code = j.code;
    } catch {
      /* not json */
    }
    if (res.status === 401) window.dispatchEvent(new CustomEvent("husk:unauthorized"));
    throw new ApiError(res.status, detail || `HTTP ${res.status}`, code);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
  form: <T>(p: string, f: FormData) => request<T>("POST", p, f, true),
};

/** POST with an SSE response body (EventSource only supports GET). */
export async function streamSSE(path: string, body: unknown, onEvent: (event: string, data: any) => void): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", "Accept-Language": currentLang() },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      let event = "message";
      const data: string[] = [];
      for (const line of part.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        try {
          onEvent(event, JSON.parse(data.join("\n")));
        } catch {
          onEvent(event, data.join("\n"));
        }
      }
    }
  }
}
