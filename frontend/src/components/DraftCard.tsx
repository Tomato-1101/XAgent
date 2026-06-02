import React from "react";
import { Badge, Card } from "./ui";
import { mediaUrl } from "../api";
import type { Draft, DraftStatus, DraftKind } from "../types";

function isVideo(path: string): boolean {
  return /\.(mp4|mov|m4v)$/i.test(path);
}

const STATUS_TONE: Record<DraftStatus, "zinc" | "sky" | "violet" | "green" | "red" | "amber"> = {
  draft: "zinc",
  approved: "sky",
  queued: "violet",
  posted: "green",
  rejected: "red",
  canceled: "amber",
};

const STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "下書き",
  approved: "承認済み",
  queued: "予約",
  posted: "投稿済み",
  rejected: "却下",
  canceled: "取消",
};

const KIND_LABEL: Record<DraftKind, string> = {
  post: "投稿",
  reply: "返信",
  quote: "引用RT",
  repost: "リポスト",
};

/** 字数(コードポイント数)。140字制限の表示に使う。 */
export function charCount(text: string): number {
  return [...text].length;
}

/** 元ポスト(返信先/引用元/リポスト元)へのリンク。handleが無くてもidで開ける。 */
function origTweetUrl(d: Draft): string | null {
  if (!d.target_tweet_id) return null;
  return d.target_handle
    ? `https://x.com/${d.target_handle}/status/${d.target_tweet_id}`
    : `https://x.com/i/web/status/${d.target_tweet_id}`;
}

/** 予約時刻(naive UTC ISO)までの相対時間。「あと約3時間」「あと2日」など。 */
function relTime(naiveUtcIso: string): string {
  const ms = new Date(naiveUtcIso + "Z").getTime() - Date.now();
  if (ms <= 0) return "まもなく";
  const min = Math.round(ms / 60000);
  if (min < 60) return `あと約${min}分`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `あと約${hr}時間`;
  return `あと約${Math.round(hr / 24)}日`;
}

export function DraftCard({ draft, actions }: { draft: Draft; actions?: React.ReactNode }) {
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="sky">{KIND_LABEL[draft.kind]}</Badge>
        <Badge tone={STATUS_TONE[draft.status]}>{STATUS_LABEL[draft.status]}</Badge>
        {draft.target_handle && <span className="text-zinc-500">→ @{draft.target_handle}</span>}
        {draft.scheduled_at && (
          <span className="text-violet-300">
            予約: {new Date(draft.scheduled_at + "Z").toLocaleString("ja-JP")}
            <span className="ml-1 text-violet-400/70">（{relTime(draft.scheduled_at)}）</span>
          </span>
        )}
        <span className="ml-auto text-zinc-600">#{draft.id}</span>
      </div>

      {/* 元ポスト本文(返信先/引用元)。人間が「何に対する案か」を判断するために表示する。 */}
      {(draft.kind === "reply" || draft.kind === "quote") && draft.target_text && (
        <div className="rounded-md border-l-2 border-zinc-600 bg-zinc-900/60 p-3 text-sm">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
            <span>{draft.kind === "reply" ? "↩ 返信先" : "❝ 引用元"}</span>
            {draft.target_handle && <span>@{draft.target_handle}</span>}
            {origTweetUrl(draft) && (
              <a
                href={origTweetUrl(draft) as string}
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 underline hover:text-sky-300"
              >
                元ポストを開く
              </a>
            )}
          </div>
          <div className="whitespace-pre-wrap text-zinc-400">{draft.target_text}</div>
        </div>
      )}

      {draft.kind === "repost" ? (
        <div className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-sm">
          <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-violet-300">
            <span>🔁 リポスト元（コメント無し）</span>
            {origTweetUrl(draft) && (
              <a
                href={origTweetUrl(draft) as string}
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 underline hover:text-sky-300"
              >
                元ポストを開く
              </a>
            )}
          </div>
          <span className="whitespace-pre-wrap text-zinc-300">
            {draft.target_text || "（自分の過去投稿）"}
          </span>
        </div>
      ) : (
        <div className="space-y-2">
          {draft.segments.map((s, i) => (
            <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-sm">
              {draft.segments.length > 1 && (
                <span className="mr-2 text-xs text-zinc-500">
                  {i + 1}/{draft.segments.length}
                </span>
              )}
              <span className="whitespace-pre-wrap">{s}</span>
              <span
                className={
                  "ml-2 text-xs " + (charCount(s) > 140 ? "text-red-400" : "text-zinc-600")
                }
              >
                {charCount(s)}字
              </span>
            </div>
          ))}
        </div>
      )}

      {draft.media_paths.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {draft.media_paths.map((p, i) =>
            isVideo(p) ? (
              <video
                key={i}
                src={mediaUrl(p)}
                className="h-20 w-20 rounded-md border border-zinc-800 object-cover"
                muted
                controls
              />
            ) : (
              <img
                key={i}
                src={mediaUrl(p)}
                alt=""
                className="h-20 w-20 rounded-md border border-zinc-800 object-cover"
              />
            )
          )}
        </div>
      )}

      {actions && <div className="flex flex-wrap gap-2 pt-1">{actions}</div>}
    </Card>
  );
}
