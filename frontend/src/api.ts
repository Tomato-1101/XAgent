import type {
  AccountProfile,
  CostResponse,
  Draft,
  DraftStatus,
  Me,
  MonitorSettings,
  PreviewResponse,
  SummaryResponse,
  Target,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ? `${res.status}: ${body.detail}` : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ status: string; version: string }>("/health"),
  me: () => req<Me>("/me"),

  preview: (text: string, allow_long = false) =>
    req<PreviewResponse>("/compose/preview", {
      method: "POST",
      body: JSON.stringify({ text, allow_long }),
    }),

  compose: (text: string, allow_long = false, emulate_handle?: string, style_guide?: string) =>
    req<Draft>("/compose", {
      method: "POST",
      body: JSON.stringify({ text, allow_long, emulate_handle, style_guide }),
    }),

  composeVariations: (
    text: string,
    n_variations: number,
    allow_long = false,
    emulate_handle?: string,
    style_guide?: string
  ) =>
    req<Draft[]>("/compose/variations", {
      method: "POST",
      body: JSON.stringify({ text, allow_long, n_variations, emulate_handle, style_guide }),
    }),

  listDrafts: (status?: DraftStatus, kind?: string) => {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    if (kind) q.set("kind", kind);
    const qs = q.toString();
    return req<Draft[]>(`/drafts${qs ? `?${qs}` : ""}`);
  },

  updateDraft: (id: number, body: { segments?: string[]; scheduled_at?: string }) =>
    req<Draft>(`/drafts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  approve: (id: number) => req<Draft>(`/drafts/${id}/approve`, { method: "POST" }),
  reject: (id: number) => req<Draft>(`/drafts/${id}/reject`, { method: "POST" }),
  queue: (id: number, mode: "optimal" | "time" = "optimal", scheduled_at?: string) =>
    req<Draft>(`/drafts/${id}/queue`, {
      method: "POST",
      body: JSON.stringify({ mode, scheduled_at }),
    }),
  postNow: (id: number) => req<Draft>(`/drafts/${id}/post`, { method: "POST" }),

  getStyle: () => req<{ guide_text: string; examples: string[] }>("/style"),
  putStyle: (guide_text: string) =>
    req<{ guide_text: string }>("/style", {
      method: "PUT",
      body: JSON.stringify({ guide_text }),
    }),
  learn: () => req<{ saved: number; me: unknown }>("/style/learn", { method: "POST" }),

  listTargets: () => req<Target[]>("/targets"),
  addTarget: (handle: string) =>
    req<Target>("/targets", { method: "POST", body: JSON.stringify({ handle }) }),
  deleteTarget: (id: number) => req<{ deleted: number }>(`/targets/${id}`, { method: "DELETE" }),

  monitorRunOnce: () =>
    req<{ reply_suggestions: number; quote_suggestions: number }>("/monitor/run-once", {
      method: "POST",
    }),
  getMonitorSettings: () => req<MonitorSettings>("/monitor/settings"),
  putMonitorSettings: (flags: Partial<MonitorSettings>) =>
    req<MonitorSettings>("/monitor/settings", {
      method: "PUT",
      body: JSON.stringify(flags),
    }),

  listProfiles: () => req<AccountProfile[]>("/profiles"),
  learnProfile: (handle: string, max_total = 200, is_self = false) =>
    req<AccountProfile>("/profiles/learn", {
      method: "POST",
      body: JSON.stringify({ handle, max_total, is_self }),
    }),

  cost: () => req<CostResponse>("/analytics/cost"),
  summary: () => req<SummaryResponse>("/analytics/summary"),
};
