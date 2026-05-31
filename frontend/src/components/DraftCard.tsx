import React from "react";
import { Badge, Card } from "./ui";
import type { Draft, DraftStatus, DraftKind } from "../types";

const STATUS_TONE: Record<DraftStatus, "zinc" | "sky" | "violet" | "green" | "red"> = {
  draft: "zinc",
  approved: "sky",
  queued: "violet",
  posted: "green",
  rejected: "red",
};

const STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "下書き",
  approved: "承認済み",
  queued: "予約",
  posted: "投稿済み",
  rejected: "却下",
};

const KIND_LABEL: Record<DraftKind, string> = {
  post: "投稿",
  reply: "返信",
  quote: "引用RT",
};

export function weighted(text: string): number {
  let w = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    const w1 =
      (cp >= 0 && cp <= 0x10ff) ||
      (cp >= 0x2000 && cp <= 0x200d) ||
      (cp >= 0x2010 && cp <= 0x201f) ||
      (cp >= 0x2032 && cp <= 0x2037);
    w += w1 ? 1 : 2;
  }
  return w;
}

export function DraftCard({ draft, actions }: { draft: Draft; actions?: React.ReactNode }) {
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="sky">{KIND_LABEL[draft.kind]}</Badge>
        <Badge tone={STATUS_TONE[draft.status]}>{STATUS_LABEL[draft.status]}</Badge>
        {draft.target_handle && <span className="text-zinc-500">→ @{draft.target_handle}</span>}
        {draft.scheduled_at && (
          <span className="text-violet-300">予約: {new Date(draft.scheduled_at + "Z").toLocaleString("ja-JP")}</span>
        )}
        <span className="ml-auto text-zinc-600">#{draft.id}</span>
      </div>

      <div className="space-y-2">
        {draft.segments.map((s, i) => (
          <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-sm">
            {draft.segments.length > 1 && (
              <span className="mr-2 text-xs text-zinc-500">
                {i + 1}/{draft.segments.length}
              </span>
            )}
            <span className="whitespace-pre-wrap">{s}</span>
            <span className="ml-2 text-xs text-zinc-600">[{weighted(s)}]</span>
          </div>
        ))}
      </div>

      {actions && <div className="flex flex-wrap gap-2 pt-1">{actions}</div>}
    </Card>
  );
}
