import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button } from "../components/ui";
import { DraftCard, weighted } from "../components/DraftCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import type { Draft, DraftStatus, Me } from "../types";

const TABS: { key: DraftStatus; label: string }[] = [
  { key: "draft", label: "下書き" },
  { key: "approved", label: "承認済み" },
  { key: "queued", label: "予約" },
  { key: "posted", label: "投稿済み" },
];

function tweetUrl(me: Me | null, id: string): string {
  return me?.username ? `https://x.com/${me.username}/status/${id}` : `https://x.com/i/web/status/${id}`;
}

type Pending = { action: "post" | "queue"; draft: Draft };

export default function Queue({ me }: { me: Me | null }) {
  const [status, setStatus] = useState<DraftStatus>("draft");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  function reload() {
    api
      .listDrafts(status, "post")
      .then(setDrafts)
      .catch((e) => setError(String(e)));
  }

  useEffect(reload, [status]);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  async function confirmPending() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.action === "post") {
        const posted = await api.postNow(pending.draft.id);
        if (posted.posted_tweet_id) {
          toast({
            tone: "success",
            message: "X に投稿しました。",
            href: tweetUrl(me, posted.posted_tweet_id),
            linkLabel: "投稿を開く",
          });
        }
      } else {
        await api.queue(pending.draft.id, "optimal");
        toast({ tone: "info", message: "最適時間に予約しました。" });
      }
      setPending(null);
      reload();
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `失敗しました: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  const total = pending ? pending.draft.segments.reduce((a, s) => a + weighted(s), 0) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Queue</h1>
        <p className="mt-1 text-sm text-zinc-500">自分の投稿の下書き・予約を管理します。</p>
      </div>

      <div className="flex gap-2">
        {TABS.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={status === t.key ? "default" : "outline"}
            onClick={() => setStatus(t.key)}
          >
            {t.label}
          </Button>
        ))}
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {drafts.length === 0 && <div className="text-sm text-zinc-500">該当なし。</div>}

      <div className="space-y-3">
        {drafts.map((d) => (
          <DraftCard
            key={d.id}
            draft={d}
            actions={
              <>
                {d.status === "draft" && (
                  <>
                    <Button size="sm" onClick={() => act(() => api.approve(d.id))}>
                      承認
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => act(() => api.reject(d.id))}>
                      却下
                    </Button>
                  </>
                )}
                {(d.status === "approved" || d.status === "queued") && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setPending({ action: "queue", draft: d })}
                    >
                      最適時間に予約
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setPending({ action: "post", draft: d })}
                    >
                      今すぐ投稿
                    </Button>
                  </>
                )}
                {d.status === "posted" && d.posted_tweet_id && (
                  <a
                    href={tweetUrl(me, d.posted_tweet_id)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-green-300 underline hover:text-green-200"
                  >
                    投稿を開く (ID: {d.posted_tweet_id})
                  </a>
                )}
              </>
            }
          />
        ))}
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.action === "post" ? "本番アカウントへ投稿します" : "最適時間に予約します"}
        description={
          pending?.action === "post"
            ? `投稿先: @${me?.username ?? "(不明)"} ・ この操作は取り消せません。`
            : `投稿先: @${me?.username ?? "(不明)"} ・ 予約時刻に自動投稿されます。`
        }
        confirmLabel={pending?.action === "post" ? "投稿する" : "予約する"}
        confirmVariant={pending?.action === "post" ? "danger" : "default"}
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <Badge tone="sky">投稿</Badge>
              <span className="text-zinc-500">加重合計 {total} / セグメント {pending.draft.segments.length}</span>
            </div>
            {pending.draft.segments.map((s, i) => (
              <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
                <span className="whitespace-pre-wrap">{s}</span>
              </div>
            ))}
          </div>
        )}
      </ConfirmDialog>
    </div>
  );
}
