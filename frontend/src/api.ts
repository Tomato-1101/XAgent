import type {
  AccountProfile,
  BlackoutSettings,
  BlackoutStatus,
  CostResponse,
  Draft,
  DraftStatus,
  Interpreted,
  ListCreateResult,
  Me,
  MediaItem,
  MonitorSettings,
  PreviewResponse,
  PromptTemplate,
  RecentPost,
  RecommendedTimes,
  SummaryResponse,
  Target,
  TemplateKind,
  XList,
  XListMember,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/** 保持パス(media/xxx.jpg)からプレビュー用URLを作る。 */
export function mediaUrl(path: string): string {
  const name = path.split("/").pop() ?? path;
  return `${BASE}/media/files/${name}`;
}

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

  compose: (
    text: string,
    allow_long = false,
    emulate_handle?: string,
    media_paths: string[] = [],
    style_guide?: string,
    raw = false,
    template_id?: number | null,
    auto_template = false
  ) =>
    req<Draft>("/compose", {
      method: "POST",
      body: JSON.stringify({
        text, allow_long, emulate_handle, media_paths, style_guide, raw,
        template_id, auto_template,
      }),
    }),

  composeVariations: (
    text: string,
    n_variations: number,
    allow_long = false,
    emulate_handle?: string,
    media_paths: string[] = [],
    style_guide?: string,
    raw = false,
    template_id?: number | null,
    auto_template = false
  ) =>
    req<Draft[]>("/compose/variations", {
      method: "POST",
      body: JSON.stringify({
        text,
        allow_long,
        n_variations,
        emulate_handle,
        media_paths,
        style_guide,
        raw,
        template_id,
        auto_template,
      }),
    }),

  // 自由文の指令を解析する(下書きは作らない。確認用)
  interpret: (text: string) =>
    req<Interpreted>("/compose/interpret", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  // 確認済みの指令から下書きを作る(引用RT or 通常投稿、整形 or そのまま)
  command: (payload: {
    action: "quote" | "reply" | "post";
    text: string;
    target_tweet_id?: string | null;
    target_handle?: string | null;
    raw?: boolean;
    allow_long?: boolean;
    emulate_handle?: string;
    media_paths?: string[];
    style_guide?: string;
    template_id?: number | null;
    auto_template?: boolean;
  }) => req<Draft>("/compose/command", { method: "POST", body: JSON.stringify(payload) }),

  // URLから引用案(引用RT)をAIに生成させる(Inboxの手動ボタン)
  quoteFromUrl: (url: string) =>
    req<Draft>("/compose/quote-from-url", { method: "POST", body: JSON.stringify({ url }) }),

  uploadMedia: async (file: File): Promise<MediaItem> => {
    const fd = new FormData();
    fd.append("file", file);
    // Content-Type は指定しない(ブラウザが multipart 境界を付ける)
    const res = await fetch(`${BASE}/media/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const b = await res.json();
        detail = b.detail ? `${res.status}: ${b.detail}` : detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json() as Promise<MediaItem>;
  },

  listDrafts: (status?: DraftStatus, kind?: string) => {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    if (kind) q.set("kind", kind);
    const qs = q.toString();
    return req<Draft[]>(`/drafts${qs ? `?${qs}` : ""}`);
  },

  // 発火できなかった予約(PCオフ等)を失効させ承認済みへ戻す。失効したdraft idを返す。
  reconcileSchedules: () =>
    req<{ missed: number[] }>("/drafts/reconcile-schedules", { method: "POST" }),

  updateDraft: (id: number, body: { segments?: string[]; scheduled_at?: string }) =>
    req<Draft>(`/drafts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  approve: (id: number) => req<Draft>(`/drafts/${id}/approve`, { method: "POST" }),
  reject: (id: number) => req<Draft>(`/drafts/${id}/reject`, { method: "POST" }),
  cancelDraft: (id: number) => req<Draft>(`/drafts/${id}/cancel`, { method: "POST" }),
  restoreDraft: (id: number) => req<Draft>(`/drafts/${id}/restore`, { method: "POST" }),
  queue: (
    id: number,
    mode: "optimal" | "time" = "optimal",
    scheduled_at?: string,
    override = false
  ) =>
    req<Draft>(`/drafts/${id}/queue`, {
      method: "POST",
      body: JSON.stringify({ mode, scheduled_at, override }),
    }),
  postNow: (id: number, override = false) =>
    req<Draft>(`/drafts/${id}/post`, {
      method: "POST",
      body: JSON.stringify({ override }),
    }),

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
  // Xリストを丸ごと対象に追加。巡回時に現メンバーへ展開され、リスト更新が自動連携される。
  addTargetList: (name: string, listId: string) =>
    req<Target>("/targets", {
      method: "POST",
      body: JSON.stringify({ kind: "list", handle: name, list_id: listId }),
    }),
  deleteTarget: (id: number) => req<{ deleted: number }>(`/targets/${id}`, { method: "DELETE" }),

  // limit を渡すとその回だけ生成数を上限管理(乱造防止)。未指定なら設定の max_drafts_per_run。
  monitorRunOnce: (limit?: number) =>
    req<{ reply_suggestions: number; quote_suggestions: number }>(
      `/monitor/run-once${limit != null ? `?limit=${limit}` : ""}`,
      { method: "POST" },
    ),
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

  recommendedTimes: () => req<RecommendedTimes>("/schedule/recommended"),

  // 自分の直近投稿(キャッシュ読み) / Xから取得 / 通常リポスト
  recentPosts: (days = 7) => req<RecentPost[]>(`/posts/recent?days=${days}`),
  refreshPosts: (days = 7) =>
    req<RecentPost[]>(`/posts/refresh?days=${days}`, { method: "POST" }),
  repost: (
    tweetId: string,
    payload: { mode: "now" | "time"; scheduled_at?: string; text?: string; override?: boolean }
  ) => req<Draft>(`/posts/${tweetId}/repost`, { method: "POST", body: JSON.stringify(payload) }),

  // 制限時間帯(ブラックアウト)設定と判定
  getBlackout: () => req<BlackoutSettings>("/schedule/blackout"),
  putBlackout: (payload: Partial<BlackoutSettings>) =>
    req<BlackoutSettings>("/schedule/blackout", { method: "PUT", body: JSON.stringify(payload) }),
  blackoutStatus: (at?: string) =>
    req<BlackoutStatus>(`/schedule/blackout/status${at ? `?at=${encodeURIComponent(at)}` : ""}`),

  cost: () => req<CostResponse>("/analytics/cost"),
  summary: () => req<SummaryResponse>("/analytics/summary"),

  // Xネイティブ「リスト」: 一覧/メンバー(読取) と 作成/更新/削除/メンバー編集(書込=公式API)
  lists: () => req<XList[]>("/lists"),
  listMembers: (id: string) => req<XListMember[]>(`/lists/${id}/members`),
  createList: (payload: {
    name: string;
    accounts: string[];
    description?: string;
    private?: boolean;
  }) => req<ListCreateResult>("/lists", { method: "POST", body: JSON.stringify(payload) }),
  updateList: (id: string, patch: { name?: string; description?: string; private?: boolean }) =>
    req<void>(`/lists/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteList: (id: string) => req<void>(`/lists/${id}`, { method: "DELETE" }),
  addListMember: (id: string, body: { handle?: string; user_id?: string }) =>
    req<void>(`/lists/${id}/members`, { method: "POST", body: JSON.stringify(body) }),
  removeListMember: (id: string, userId: string) =>
    req<void>(`/lists/${id}/members/${userId}`, { method: "DELETE" }),

  // 投稿/リプの「型」: 複数保存し、Composeで選択 or「AIに任せる」で使う
  templates: (kind?: TemplateKind) =>
    req<PromptTemplate[]>(`/templates${kind ? `?kind=${kind}` : ""}`),
  createTemplate: (payload: { name: string; kind: TemplateKind; body: string; active?: boolean }) =>
    req<PromptTemplate>("/templates", { method: "POST", body: JSON.stringify(payload) }),
  updateTemplate: (
    id: number,
    patch: { name?: string; body?: string; kind?: TemplateKind; active?: boolean }
  ) => req<PromptTemplate>(`/templates/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  activateTemplate: (id: number) =>
    req<PromptTemplate>(`/templates/${id}/activate`, { method: "POST" }),
  deleteTemplate: (id: number) => req<void>(`/templates/${id}`, { method: "DELETE" }),
};
