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
  MetricsSettings,
  MetricsSummary,
  MonitorSettings,
  NewsRecentResponse,
  NewsSettings,
  PostSettings,
  PreviewResponse,
  PromptTemplate,
  RecentPost,
  RecommendedTimes,
  SchedulerStatus,
  SummaryResponse,
  Target,
  TemplateKind,
  TwitterApiKey,
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
  progress: string | null;
  elapsed_seconds: number;
  result: unknown;
  error: string | null;
}

/** 実行中ジョブの進捗通知。message は「何をしているか」、elapsedSeconds はサーバ計測の経過秒。 */
export type JobProgress = (message: string, elapsedSeconds: number) => void;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * 既知の job_id を完了までポーリングして結果を返す。ポーリング中の一時的な接続断は
 * リトライ継続し、404(サーバ再起動でジョブ消滅)だけ中断として扱う。
 * onProgress でサーバ側の進捗(何をしているか+経過秒)を受け取れる(エラーと待ち時間の区別用)。
 * 画面を開き直した時に実行中ジョブへ再接続する用途でも使う。
 */
async function pollJob<T>(job_id: string, onProgress?: JobProgress): Promise<T> {
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
    onProgress?.(j.progress ?? "", j.elapsed_seconds);
  }
}

/**
 * 長時間のAI生成をジョブAPIで実行する。POST で job_id を受け取り /jobs/{id} を
 * ポーリングして結果を返す(同期fetchだとブラウザの約300秒上限・CLIタイムアウト・
 * サーバ再起動で「Failed to fetch」になるため)。
 */
async function runJob<T>(path: string, init?: RequestInit, onProgress?: JobProgress): Promise<T> {
  const { job_id } = await req<{ job_id: string }>(path, init);
  return pollJob<T>(job_id, onProgress);
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
  // opts.userPrompt で方向性を指示、opts.count で案の数(1〜5)を指定。result は drafts 配列。
  quoteFromUrl: (
    url: string,
    opts?: { userPrompt?: string; count?: number },
    onProgress?: JobProgress,
  ) =>
    runJob<{ drafts: Draft[] }>(
      "/compose/quote-from-url",
      {
        method: "POST",
        body: JSON.stringify({
          url,
          user_prompt: opts?.userPrompt || undefined,
          count: opts?.count ?? 1,
        }),
      },
      onProgress,
    ),

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
  recast: (id: number, to: "reply" | "quote", onProgress?: JobProgress) =>
    runJob<Draft>(
      `/drafts/${id}/recast`,
      { method: "POST", body: JSON.stringify({ to }) },
      onProgress,
    ),
  // 絡みの下書きの本文を、同じ型のままウェブ検索つきでAIに作り直させる(リプ案の再生成)。ジョブ経由。
  // userPrompt で再生成の方向性を指示できる(空ならお任せ)。
  regenerateDraft: (id: number, userPrompt?: string, onProgress?: JobProgress) =>
    runJob<Draft>(
      `/drafts/${id}/regenerate`,
      { method: "POST", body: JSON.stringify({ user_prompt: userPrompt || undefined }) },
      onProgress,
    ),

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
  // 候補収集+バッチAI選定で数分〜15分かかるためジョブ開始のみ(追跡は pollJob で行う。
  // 画面を開き直しても runningJobs→pollJob で実行中の生成へ再接続できるよう分離)。
  monitorRunOnceStart: (limit?: number) =>
    req<{ job_id: string }>(`/monitor/run-once${limit != null ? `?limit=${limit}` : ""}`, {
      method: "POST",
    }),
  // 実行中ジョブの一覧。画面表示時に進行中の生成があれば再接続して進捗を出すために使う。
  runningJobs: () =>
    req<{ job_id: string; label: string | null; progress: string | null; elapsed_seconds: number }[]>(
      "/jobs",
    ),
  pollJob,
  // 有名人ウォッチを1回実行(検索1〜2クエリ+ヒット時のみAI生成)。ジョブ開始のみ(追跡は pollJob)。
  celebRunOnceStart: () =>
    req<{ job_id: string }>("/monitor/celeb-run-once", { method: "POST" }),
  // バズウォッチを1回実行(min_faves検索1クエリ+ヒット時のみAI生成)。ジョブ開始のみ(追跡は pollJob)。
  buzzRunOnceStart: () =>
    req<{ job_id: string }>("/monitor/buzz-run-once", { method: "POST" }),
  getMonitorSettings: () => req<MonitorSettings>("/monitor/settings"),
  putMonitorSettings: (flags: Partial<MonitorSettings>) =>
    req<MonitorSettings>("/monitor/settings", {
      method: "PUT",
      body: JSON.stringify(flags),
    }),

  // ニュース速報(XNewsBot連携): 新着から速報下書きを生成。ジョブ開始のみ(追跡は pollJob)。
  newsRunOnceStart: (limit?: number) =>
    req<{ job_id: string }>(`/news/run-once${limit != null ? `?limit=${limit}` : ""}`, {
      method: "POST",
    }),
  newsRecent: () => req<NewsRecentResponse>("/news/recent"),
  getNewsSettings: () => req<NewsSettings>("/news/settings"),
  putNewsSettings: (values: Partial<NewsSettings>) =>
    req<NewsSettings>("/news/settings", { method: "PUT", body: JSON.stringify(values) }),

  // オリジナル投稿の叩き台生成(A): テーマ角度から型に沿った下書きを生成。ジョブ開始のみ(追跡は pollJob)。
  postGenRunOnceStart: (limit?: number) =>
    req<{ job_id: string }>(`/post-gen/run-once${limit != null ? `?limit=${limit}` : ""}`, {
      method: "POST",
    }),
  getPostGenSettings: () => req<PostSettings>("/post-gen/settings"),
  putPostGenSettings: (values: Partial<PostSettings>) =>
    req<PostSettings>("/post-gen/settings", { method: "PUT", body: JSON.stringify(values) }),

  // 自分の投稿メトリクス(B): 公式APIで取得・保存(読み取りのみ)。ジョブ開始のみ。集計は metricsSummary。
  metricsRunOnceStart: () => req<{ job_id: string }>("/metrics/run-once", { method: "POST" }),
  metricsSummary: () => req<MetricsSummary>("/metrics/summary"),
  getMetricsSettings: () => req<MetricsSettings>("/metrics/settings"),
  putMetricsSettings: (values: Partial<MetricsSettings>) =>
    req<MetricsSettings>("/metrics/settings", { method: "PUT", body: JSON.stringify(values) }),

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

  // twitterapi.io 読み取りキー(複数・優先度順フォールバック)。平文キーは保存時のみ送る。
  twitterApiKeys: () => req<TwitterApiKey[]>("/twitterapi-keys"),
  createTwitterApiKey: (payload: { api_key: string; label?: string; enabled?: boolean }) =>
    req<TwitterApiKey>("/twitterapi-keys", { method: "POST", body: JSON.stringify(payload) }),
  updateTwitterApiKey: (
    id: number,
    patch: { label?: string; api_key?: string; priority?: number; enabled?: boolean }
  ) => req<TwitterApiKey>(`/twitterapi-keys/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteTwitterApiKey: (id: number) =>
    req<void>(`/twitterapi-keys/${id}`, { method: "DELETE" }),
  // この id 順に優先度を 0,1,2... へ振り直す(↑↓の並べ替え)。
  reorderTwitterApiKeys: (ids: number[]) =>
    req<TwitterApiKey[]>("/twitterapi-keys/reorder", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  // キー1本で疎通を1回確認し、結果(last_ok_at/last_error)を記録して返す。
  testTwitterApiKey: (id: number) =>
    req<TwitterApiKey>(`/twitterapi-keys/${id}/test`, { method: "POST" }),
};
