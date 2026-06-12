export type DraftStatus = "draft" | "approved" | "queued" | "posted" | "rejected" | "canceled";
export type DraftKind = "post" | "reply" | "quote" | "repost";

export interface Draft {
  id: number;
  kind: DraftKind;
  status: DraftStatus;
  source_text: string;
  segments: string[];
  media_paths: string[];
  target_tweet_id: string | null;
  target_handle: string | null;
  target_text: string; // 絡む相手の元ポスト本文(reply/quote/repost の表示用)
  target_created_at: string | null; // 元ポストの投稿時刻(naive UTC ISO / 取得できた時のみ)
  scheduled_at: string | null;
  posted_at: string | null;
  posted_tweet_id: string | null;
  blackout_override: boolean;
  schedule_missed: boolean; // 予約失効(PCオフ等で発火できず)→再予約を促す印
  created_at: string;
  updated_at: string;
}

export interface PreviewSegment {
  text: string;
  weighted_length: number;
  char_length: number;
}

export interface PreviewResponse {
  weighted_length: number;
  char_length: number;
  folded: boolean;
  over_limit: boolean;
  segments: PreviewSegment[];
}

export interface Target {
  id: number;
  kind: string;
  handle: string | null;
  user_id: string | null;
  list_id: string | null;
  keyword: string | null;
  active: boolean;
  notes: string | null;
}

export interface CostKindStat {
  units: number;
  cost_usd: number;
}

export interface CostGroup {
  cost_usd: number;
  units: number;
  by_kind: Record<string, CostKindStat>;
}

export interface CostResponse {
  total_usd: number;
  by_kind: Record<string, CostKindStat>;
  x_api: CostGroup;
  claude_api: CostGroup;
}

export interface SummaryResponse {
  draft_counts: Record<string, number>;
}

export interface Me {
  id: string | null;
  username: string | null;
}

// 予約スケジューラ(予約投稿の常駐)の稼働状況。/status の戻り。
export interface SchedulerStatus {
  scheduler_enabled: boolean;
  scheduler_running: boolean;
  healthy: boolean; // 予約発火ループが生きているか(ハートビート鮮度＋稼働)
  last_queue_tick_at: string | null; // 直近のqueue_tick発火時刻(naive UTC ISO)
  seconds_since_queue_tick: number | null;
  queue_interval_seconds: number;
  next_queue_run_at: string | null;
  posting_enabled: boolean; // 緊急停止(false=全投稿停止)
  auto_monitor_enabled: boolean; // 絡み案の自動生成トグル
}

export interface AccountProfile {
  id: number;
  handle: string;
  user_id: string | null;
  is_self: boolean;
  display_name: string | null;
  posts_fetched: number;
  avg_likes: number;
  avg_retweets: number;
  active_hours_json: string;
  profile_json: string;
  profile_text: string;
  extracted_at: string;
  updated_at: string;
}

export interface MonitorSettings {
  id?: number;
  mentions_enabled: boolean;
  manual_targets_enabled: boolean;
  keyword_search_enabled: boolean;
  following_enabled: boolean;
  auto_monitor_enabled: boolean; // デーモン: 自動監視(絡み案生成)
  auto_post_enabled: boolean; // デーモン: 予約分の自動投稿
  max_drafts_per_run: number; // 1監視サイクルの総生成数上限
  updated_at?: string;
}

export interface NewsSettings {
  id?: number;
  auto_news_enabled: boolean; // ダイジェスト連動の自動下書き生成(既定OFF)
  genres_json: string; // 対象ジャンル(JSON配列文字列)
  max_posts_per_run: number; // 1回の生成上限
  last_digest_id: number; // 処理済みのXNewsBotダイジェストID
  updated_at?: string;
}

export interface NewsItem {
  index: number;
  digest_id: number;
  digest_date: string;
  slot: string;
  genre: string;
  importance: string;
  title: string;
  summary: string;
  detail: string;
  source_url: string | null;
  source_tweet: { url: string; author: string; text: string; views: number } | null;
  top_view_count: number;
}

export interface NewsRecentResponse {
  available: boolean;
  error: string | null;
  items: NewsItem[];
  last_digest_id: number;
}

export type MediaKind = "image" | "video" | "other";

export interface MediaItem {
  path: string; // サーバ保持パス(投稿時に使用)
  kind: MediaKind;
  filename: string;
}

export interface RecommendedSlot {
  hour: number;
  label: string;
  tier: "best" | "great" | "good";
}

export interface RecommendedTimes {
  slots: RecommendedSlot[];
  note: string;
  sources: string[];
  next_slots: string[]; // naive UTC ISO
}

export interface Interpreted {
  action: "quote" | "post";
  target_url: string | null;
  target_tweet_id: string | null;
  target_handle: string | null;
  body: string;
  raw: boolean;
  note: string;
}

export interface RecentPost {
  tweet_id: string;
  text: string;
  created_at: string | null;
  like_count: number;
  retweet_count: number;
  url: string;
}

export interface BlackoutSettings {
  enabled: boolean;
  weekdays: number[]; // 月=0..日=6
  windows: [string, string][]; // [["09:00","12:00"], ...]
  updated_at?: string | null;
}

export interface BlackoutStatus {
  blackout: boolean;
  reason: string;
  at: string;
}

// Xネイティブの「リスト」
export interface XList {
  id: string;
  name: string;
  description: string;
  private: boolean;
  member_count: number;
}

export interface XListMember {
  id: string;
  username: string | null;
  name: string | null;
  description: string;
  profile_image_url: string | null;
  followers_count: number;
}

export interface ListCreateResult {
  list_id: string;
  url: string;
  name: string;
  added: string[];
  skipped: { handle: string; reason: string }[];
}

export type TemplateKind = "post" | "reply" | "quote" | "news";

export interface PromptTemplate {
  id: number;
  name: string;
  kind: TemplateKind;
  body: string;
  active: boolean;
  builtin: boolean;
}
