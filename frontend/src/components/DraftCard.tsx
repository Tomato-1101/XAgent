import React, { useState } from "react";
import { Badge, Button, Card, Spinner, Textarea } from "./ui";
import { api, mediaUrl } from "../api";
import type { Draft, DraftStatus, DraftKind } from "../types";

// 本文(セグメント)を編集できる状態。投稿後/却下/取消は編集不可。リポストは本文を持たない。
const EDITABLE_STATUSES: DraftStatus[] = ["draft", "approved", "queued"];

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

/** naive UTC ISO を日本時間の絶対表記(短縮)に。null は null のまま。 */
function fmtAbs(naiveUtcIso: string | null | undefined): string | null {
  if (!naiveUtcIso) return null;
  return new Date(naiveUtcIso + "Z").toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 過去の naive UTC ISO からの経過。「約3時間前」など(案・元投稿の鮮度判断用)。 */
function agoTime(naiveUtcIso: string): string {
  const ms = Date.now() - new Date(naiveUtcIso + "Z").getTime();
  if (ms < 0) return "これから";
  const min = Math.round(ms / 60000);
  if (min < 60) return `約${min}分前`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `約${hr}時間前`;
  return `約${Math.round(hr / 24)}日前`;
}

export function DraftCard({
  draft,
  actions,
  onUpdated,
}: {
  draft: Draft;
  actions?: React.ReactNode;
  /** 本文を編集・保存したら呼ばれる(親で一覧を再取得する用)。未指定なら編集ボタンを出さない。 */
  onUpdated?: (d: Draft) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [segs, setSegs] = useState<string[]>(draft.segments);
  const [saving, setSaving] = useState(false);
  const [editErr, setEditErr] = useState<string | null>(null);

  const canEdit =
    !!onUpdated && draft.kind !== "repost" && EDITABLE_STATUSES.includes(draft.status);

  function startEdit() {
    setSegs(draft.segments);
    setEditErr(null);
    setEditing(true);
  }

  async function save() {
    setSaving(true);
    setEditErr(null);
    try {
      const updated = await api.updateDraft(draft.id, { segments: segs });
      setEditing(false);
      onUpdated?.(updated);
    } catch (e) {
      setEditErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="sky">{KIND_LABEL[draft.kind]}</Badge>
        <Badge tone={STATUS_TONE[draft.status]}>{STATUS_LABEL[draft.status]}</Badge>
        {draft.schedule_missed && (
          <span title="予約時刻にPCがオフ等で投稿できず失効しました。承認済みに戻したので再予約してください。">
            <Badge tone="amber">予約失効</Badge>
          </span>
        )}
        {draft.target_handle && <span className="text-zinc-500">→ @{draft.target_handle}</span>}
        {draft.scheduled_at && (
          <span className="text-violet-300">
            予約: {new Date(draft.scheduled_at + "Z").toLocaleString("ja-JP")}
            <span className="ml-1 text-violet-400/70">（{relTime(draft.scheduled_at)}）</span>
          </span>
        )}
        <span className="ml-auto text-zinc-600">#{draft.id}</span>
        {canEdit && !editing && (
          <button
            onClick={startEdit}
            className="text-xs text-sky-400 hover:text-sky-300"
            title="本文(返信文/投稿文)を編集する"
          >
            ✎ 編集
          </button>
        )}
      </div>

      {/* 時刻系: 案の生成時刻(全種)・元投稿の投稿時刻(絡み案)・投稿済み時刻。鮮度の判断に使う。 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
        <span>🕒 生成 {fmtAbs(draft.created_at)}（{agoTime(draft.created_at)}）</span>
        {draft.target_created_at &&
          (draft.kind === "reply" || draft.kind === "quote" || draft.kind === "repost") && (
            <span>
              元投稿 {fmtAbs(draft.target_created_at)}（{agoTime(draft.target_created_at)}）
            </span>
          )}
        {draft.posted_at && (
          <span className="text-green-400/80">投稿済 {fmtAbs(draft.posted_at)}</span>
        )}
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
      ) : editing ? (
        <div className="space-y-2">
          {segs.map((s, i) => (
            <div key={i} className="space-y-1">
              {segs.length > 1 && (
                <div className="text-xs text-zinc-500">
                  {i + 1}/{segs.length} 通目
                </div>
              )}
              <Textarea
                rows={Math.min(8, Math.max(2, Math.ceil(((s.length || 1) + 1) / 40)))}
                value={s}
                onChange={(e) =>
                  setSegs((prev) => prev.map((v, j) => (j === i ? e.target.value : v)))
                }
              />
              <div
                className={
                  "text-right text-xs " + (charCount(s) > 140 ? "text-red-400" : "text-zinc-600")
                }
              >
                {charCount(s)}字
              </div>
            </div>
          ))}
          {editErr && <div className="text-xs text-red-300">保存エラー: {editErr}</div>}
          <div className="flex gap-2">
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? <Spinner /> : "保存"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              取消
            </Button>
          </div>
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
