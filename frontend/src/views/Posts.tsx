import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Spinner } from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SchedulePicker } from "../components/SchedulePicker";
import { useToast } from "../components/Toast";
import { useBlackoutGate } from "../components/BlackoutGate";
import type { RecentPost, RecommendedSlot } from "../types";

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso + "Z").toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** datetime-local 値(JST壁時計)を +09:00 付きISOにする。 */
function toScheduledAtISO(value: string): string {
  return `${value}:00+09:00`;
}

type Pending = { post: RecentPost; mode: "now" | "time" };

export default function Posts() {
  const [posts, setPosts] = useState<RecentPost[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [schedAt, setSchedAt] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<RecommendedSlot[]>([]);
  const toast = useToast();
  const { gate, element: blackoutGate } = useBlackoutGate();

  function load() {
    api
      .recentPosts(7)
      .then(setPosts)
      .catch((e) => setError(String(e)));
  }

  useEffect(load, []);
  useEffect(() => {
    api.recommendedTimes().then((r) => setRec(r.slots)).catch(() => setRec([]));
  }, []);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setPosts(await api.refreshPosts(7));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  function openRepostNow(post: RecentPost) {
    setPending({ post, mode: "now" });
  }

  function openRepostTime(post: RecentPost) {
    // 既定値は1時間後(JST壁時計)
    const now = new Date(Date.now() + 60 * 60 * 1000);
    const fmt = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
    setSchedAt(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`);
    setPending({ post, mode: "time" });
  }

  async function doRepost(override: boolean) {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.mode === "time") {
        await api.repost(pending.post.tweet_id, {
          mode: "time",
          scheduled_at: toScheduledAtISO(schedAt),
          text: pending.post.text,
          override,
        });
        toast({ tone: "info", message: `JST ${schedAt.replace("T", " ")} にリポストを予約しました。` });
      } else {
        await api.repost(pending.post.tweet_id, {
          mode: "now",
          text: pending.post.text,
          override,
        });
        toast({ tone: "success", message: "リポストしました。" });
      }
      setPending(null);
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `失敗しました: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  // 確認ダイアログの「実行」→ 制限帯ゲートを通してから実際にリポスト
  function confirmPending() {
    if (!pending) return;
    const at = pending.mode === "time" ? toScheduledAtISO(schedAt) : undefined;
    gate((override) => doRepost(override), at);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">Posts</h1>
          <p className="mt-1 text-sm text-zinc-500">
            自分の直近1週間の投稿。通常リポスト（コメント無し）で再拡散できます。
          </p>
        </div>
        <Button onClick={refresh} disabled={loading} variant="outline">
          {loading ? <Spinner /> : "Xから更新"}
        </Button>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {posts.length === 0 && (
        <div className="text-sm text-zinc-500">
          直近の投稿がありません。「Xから更新」で取得してください。
        </div>
      )}

      <div className="space-y-3">
        {posts.map((p) => (
          <Card key={p.tweet_id} className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              <span>{fmtDate(p.created_at)}</span>
              <Badge tone="zinc">♥ {p.like_count}</Badge>
              <Badge tone="zinc">RT {p.retweet_count}</Badge>
              <a
                href={p.url}
                target="_blank"
                rel="noreferrer"
                className="ml-auto text-sky-300 underline hover:text-sky-200"
              >
                Xで開く
              </a>
            </div>
            <div className="whitespace-pre-wrap text-sm text-zinc-100">{p.text}</div>
            <div className="flex flex-wrap gap-2 pt-1">
              <Button size="sm" variant="danger" onClick={() => openRepostNow(p)}>
                今すぐリポスト
              </Button>
              <Button size="sm" variant="outline" onClick={() => openRepostTime(p)}>
                時間指定でリポスト
              </Button>
            </div>
          </Card>
        ))}
      </div>

      {/* 即時リポスト確認 */}
      <ConfirmDialog
        open={pending?.mode === "now"}
        title="この投稿を通常リポストします"
        description="コメント無しで再拡散します（引用ではありません）。"
        confirmLabel="リポストする"
        confirmVariant="danger"
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <div className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
            <span className="whitespace-pre-wrap">{pending.post.text}</span>
          </div>
        )}
      </ConfirmDialog>

      {/* 時間指定リポスト */}
      <ConfirmDialog
        open={pending?.mode === "time"}
        title="時間を指定してリポスト（日本時間）"
        description="指定したJST時刻に自動でリポストします。"
        confirmLabel="この時刻で予約"
        confirmVariant="default"
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        <div className="space-y-3">
          {pending && (
            <div className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
              <span className="whitespace-pre-wrap">{pending.post.text}</span>
            </div>
          )}
          <SchedulePicker value={schedAt} onChange={setSchedAt} recommended={rec} />
        </div>
      </ConfirmDialog>

      {blackoutGate}
    </div>
  );
}
