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
  SchedulerStatus,
  SummaryResponse,
  Target,
  TemplateKind,
  XList,
  XListMember,
} from "./types";

// 本番(FastAPIが frontend/dist を配信=same-origin)は相対パス、vite dev(:5180)時のみ :8000 へ。
const BASE =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

// API_TOKEN 認証(サーバ側で設定時のみ有効)。端末ごとに localStorage に保持する。
const TOKEN_KEY = "xagent_api_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { "X-API-Token": token } : {};
}

/** 保持パス(media/xxx.jpg)からプレビュー用URLを作る。 */
export function mediaUrl(path: string): string {
  const name = path.split("/").pop() ?? path;
  return `${BASE}/media/files/${name}`;
}

/** HTTPステータス付きエラー。接続自体の失敗(TypeError)と区別するために使う。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...authHeaders() },
      ...init,
    });
  } catch {
    // fetch の TypeError(接続拒否・切断・タイムアウト)。素のメッセージは不親切なので変換。
    throw new Error("サーバに接続できません(再起動中かネットワーク断)。少し待って再試行してください。");
  }
  if (!res.ok) {
    // トークン不正/未入力。App がこのイベントを拾ってログイン画面を出す。
    if (res.status === 401) window.dispatchEvent(new Event("xagent:unauthorized"));
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ? `${res.status}: ${body.detail}` : detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

interface JobStatus {
  job_id: string;
  status: "running" | "done" | "error";
  result: unknown;
  error: string | null;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * 長時間のAI生成をジョブAPIで実行する。POST で job_id を受け取り /jobs/{id} を
 * ポーリングして結果を返す(同期fetchだとブラウザの約300秒上限・CLIタイムアウト・
 * サーバ再起動で「Failed to fetch」になるため)。ポーリング中の一時的な接続断は
 * リトライ継続し、404(サーバ再起動でジョブ消滅)だけ中断として扱う。
 */
async function runJob<T>(path: string, init?: RequestInit): Promise<T> {
  const { job_id } = await req<{ job_id: string }>(path, init);
  for (;;) {
    await sleep(2000);
    let j: JobStatus;
    try {
      j = await req<JobStatus>(`/jobs/${job_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        throw new Error("サーバが再起動したため生成が中断されました。もう一度実行してください。");
      }
      if (e instanceof ApiError) throw e; // 401 など。接続断(plain Error)のみ継続
      continue;
    }
    if (j.status === "done") return j.result as T;
    if (j.status === "error") throw new Error(j.error ?? "生成に失敗しました。");
  }
}

export const api = {
  health: () => req<{ status: string; version: string }>("/health"),
  // 予約スケジューラ(予約投稿の常駐)の稼働状況。UIのバッジ表示に使う。
  status: () => req<SchedulerStatus>("/status"),
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

  // URLから引用案(引用RT)をAIに生成させる(Inboxの手動ボタン)。ジョブ経由(数十秒〜数分)。
  quoteFromUrl: (url: string) =>
    runJob<Draft>("/compose/quote-from-url", { method: "POST", body: JSON.stringify({ url }) }),

  uploadMedia: async (file: File): Promise<MediaItem> => {
    const fd = new FormData();
    fd.append("file", file);
    // Content-Type は指定しない(ブラウザが multipart 境界を付ける)
    const res = await fetch(`${BASE}/media/upload`, {
      method: "POST",
      headers: authHeaders(),
      body: fd,
    });
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event("xagent:unauthorized"));
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
  // 絡みの下書きをリプライ⇄引用RTに作り直す(本文を再生成し型を切替)。Inboxの手動切替ボタン。
  recast: (id: number, to: "reply" | "quote") =>
    runJob<Draft>(`/drafts/${id}/recast`, { method: "POST", body: JSON.stringify({ to }) }),

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
  // 候補収集+バッチAI選定で数分〜15分かかるためジョブ経由。
  monitorRunOnce: (limit?: number) =>
    runJob<{ reply_suggestions: number; quote_suggestions: number }>(
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
