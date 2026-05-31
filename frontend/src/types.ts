export type DraftStatus = "draft" | "approved" | "queued" | "posted" | "rejected";
export type DraftKind = "post" | "reply" | "quote";

export interface Draft {
  id: number;
  kind: DraftKind;
  status: DraftStatus;
  source_text: string;
  segments: string[];
  media_paths: string[];
  target_tweet_id: string | null;
  target_handle: string | null;
  scheduled_at: string | null;
  posted_at: string | null;
  posted_tweet_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface PreviewSegment {
  text: string;
  weighted_length: number;
}

export interface PreviewResponse {
  weighted_length: number;
  folded: boolean;
  over_limit: boolean;
  segments: PreviewSegment[];
}

export interface Target {
  id: number;
  kind: string;
  handle: string | null;
  user_id: string | null;
  keyword: string | null;
  active: boolean;
  notes: string | null;
}

export interface CostResponse {
  total_usd: number;
  by_kind: Record<string, { units: number; cost_usd: number }>;
}

export interface SummaryResponse {
  draft_counts: Record<string, number>;
}

export interface Me {
  id: string | null;
  username: string | null;
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
  updated_at?: string;
}
