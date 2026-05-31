import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Spinner } from "../components/ui";
import { DraftCard, weighted } from "../components/DraftCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import type { Draft, Me } from "../types";

function tweetUrl(me: Me | null, id: string): string {
  return me?.username ? `https://x.com/${me.username}/status/${id}` : `https://x.com/i/web/status/${id}`;
}

const KIND_LABEL: Record<string, string> = { reply: "返信", quote: "引用RT", post: "投稿" };

export default function Inbox({ me }: { me: Me | null }) {
  const [items, setItems] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [pending, setPending] = useState<Draft | null>(null);
  const toast = useToast();

  function reload() {
    api
      .listDrafts("draft")
      .then((all) => setItems(all.filter((d) => d.kind !== "post")))
      .catch((e) => setError(String(e)));
  }

  useEffect(reload, []);

  async function runMonitor() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await api.monitorRunOnce();
      setInfo(`返信案 ${r.reply_suggestions} 件 / 絡み案 ${r.quote_suggestions} 件を生成`);
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  async function confirmSend() {
    if (!pending) return;
    setSending(true);
    setError(null);
    try {
      await api.approve(pending.id);
      const posted = await api.postNow(pending.id);
      if (posted.posted_tweet_id) {
        toast({
          tone: "success",
          message: `${KIND_LABEL[pending.kind] ?? "投稿"}を送信しました。`,
          href: tweetUrl(me, posted.posted_tweet_id),
          linkLabel: "投稿を開く",
        });
      }
      setPending(null);
      reload();
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `失敗しました: ${String(e)}` });
    } finally {
      setSending(false);
    }
  }

  const total = pending ? pending.segments.reduce((a, s) => a + weighted(s), 0) : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">Inbox</h1>
          <p className="mt-1 text-sm text-zinc-500">
            メンションへの返信案・絡み案（承認すれば送信できます）。
          </p>
        </div>
        <Button onClick={runMonitor} disabled={busy} variant="outline">
          {busy ? <Spinner /> : "監視を1回実行"}
        </Button>
      </div>

      {info && <div className="text-sm text-sky-300">{info}</div>}
      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {items.length === 0 && <div className="text-sm text-zinc-500">承認待ちの案はありません。</div>}

      <div className="space-y-3">
        {items.map((d) => (
          <DraftCard
            key={d.id}
            draft={d}
            actions={
              <>
                <Button size="sm" variant="danger" onClick={() => setPending(d)}>
                  承認して送信
                </Button>
                <Button size="sm" variant="outline" onClick={() => act(() => api.approve(d.id))}>
                  承認のみ
                </Button>
                <Button size="sm" variant="ghost" onClick={() => act(() => api.reject(d.id))}>
                  却下
                </Button>
              </>
            }
          />
        ))}
      </div>

      <ConfirmDialog
        open={pending !== null}
        title="本番アカウントから送信します"
        description={`投稿先: @${me?.username ?? "(不明)"}${
          pending?.target_handle ? ` → @${pending.target_handle} 宛` : ""
        } ・ この操作は取り消せません。`}
        confirmLabel="送信する"
        confirmVariant="danger"
        busy={sending}
        onConfirm={confirmSend}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <Badge tone="sky">{KIND_LABEL[pending.kind] ?? pending.kind}</Badge>
              <span className="text-zinc-500">加重合計 {total}</span>
            </div>
            {pending.segments.map((s, i) => (
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
