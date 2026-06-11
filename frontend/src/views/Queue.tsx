import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card } from "../components/ui";
import { DraftCard, charCount } from "../components/DraftCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SchedulePicker } from "../components/SchedulePicker";
import { useBlackoutGate } from "../components/BlackoutGate";
import { useAutoRefresh } from "../components/useAutoRefresh";
import { useToast } from "../components/Toast";
import type { Draft, DraftStatus, Me, RecommendedSlot, RecommendedTimes } from "../types";

const TABS: { key: DraftStatus; label: string }[] = [
  { key: "draft", label: "下書き" },
  { key: "approved", label: "承認済み" },
  { key: "queued", label: "予約" },
  { key: "posted", label: "投稿済み" },
  { key: "canceled", label: "取消（ゴミ箱）" },
];

const TIER_TONE: Record<RecommendedSlot["tier"], "sky" | "violet" | "zinc"> = {
  best: "sky",
  great: "violet",
  good: "zinc",
};

function tweetUrl(me: Me | null, id: string): string {
  return me?.username ? `https://x.com/${me.username}/status/${id}` : `https://x.com/i/web/status/${id}`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** 現在時刻のJST年月日時分。 */
function jstNow() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const p = Object.fromEntries(fmt.formatToParts(new Date()).map((x) => [x.type, x.value]));
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour, mi: +p.minute };
}

/** 指定JST時(hour)の次の発生を datetime-local 値("YYYY-MM-DDTHH:00")で返す。 */
function jstSlotValue(hour: number): string {
  const n = jstNow();
  let y = n.y,
    mo = n.mo,
    d = n.d;
  if (hour <= n.h) {
    const dt = new Date(Date.UTC(n.y, n.mo - 1, n.d));
    dt.setUTCDate(dt.getUTCDate() + 1);
    y = dt.getUTCFullYear();
    mo = dt.getUTCMonth() + 1;
    d = dt.getUTCDate();
  }
  return `${y}-${pad(mo)}-${pad(d)}T${pad(hour)}:00`;
}

/** datetime-local 値(JST壁時計)を +09:00 付きISOにして送る。 */
function toScheduledAtISO(value: string): string {
  return `${value}:00+09:00`;
}

type Pending = { action: "post" | "optimal" | "cancel"; draft: Draft };

export default function Queue({ me }: { me: Me | null }) {
  const [status, setStatus] = useState<DraftStatus>("draft");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [schedDraft, setSchedDraft] = useState<Draft | null>(null);
  const [schedAt, setSchedAt] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<RecommendedTimes | null>(null);
  const [queuedCount, setQueuedCount] = useState(0);
  const toast = useToast();
  const { gate, element: blackoutGate } = useBlackoutGate();

  function refreshCount() {
    api.listDrafts("queued").then((r) => setQueuedCount(r.length)).catch(() => {});
  }

  function reload() {
    // 下書きタブのみ自分の投稿(post)に限定(返信/引用の下書きはInbox側で扱う)。
    // 返信(reply)はAPI投稿できずInboxで手動送信するため、全タブでQueueから分離する。
    const kind = status === "draft" ? "post" : undefined;
    api
      .listDrafts(status, kind)
      .then((rows0) => {
        const rows = rows0.filter((d) => d.kind !== "reply");
        if (status === "queued") {
          const sorted = [...rows].sort((a, b) =>
            (a.scheduled_at ?? "").localeCompare(b.scheduled_at ?? "")
          );
          setQueuedCount(sorted.length);
          setDrafts(sorted);
        } else {
          setDrafts(rows);
          refreshCount();
        }
      })
      .catch((e) => setError(String(e)));
  }

  // 予約/承認済みタブを開いたら、PCオフ等で発火できなかった予約を失効させる(承認済みへ戻す)。
  // デーモンが動いていない間に過ぎた予約を、遅れて投稿せず再予約待ちにするための復旧処理。
  useEffect(() => {
    if (status !== "queued" && status !== "approved") return;
    api
      .reconcileSchedules()
      .then((r) => {
        if (r.missed.length > 0) {
          toast({
            tone: "info",
            message: `予約 ${r.missed.length} 件が時刻超過で失効しました。「承認済み」タブで再予約してください。`,
          });
          reload();
        }
      })
      .catch(() => {});
    // statusが変わるたびに1度だけ実行する(reload/toastは安定参照でないため依存に含めない)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  useEffect(reload, [status]);
  // スマホでタブ復帰した時や予約発火後に手動リロードしなくても一覧が追従するように
  useAutoRefresh(reload);
  useEffect(() => {
    api.recommendedTimes().then(setRec).catch(() => setRec(null));
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  function confirmPending() {
    if (!pending) return;
    // 今すぐ投稿は制限時間帯の二段階確認を通す。取消・最適予約はそのまま実行。
    if (pending.action === "post") {
      const draft = pending.draft;
      gate((override) => doPost(draft, override));
      return;
    }
    runPending();
  }

  async function doPost(draft: Draft, override: boolean) {
    setBusy(true);
    setError(null);
    try {
      const posted = await api.postNow(draft.id, override);
      if (posted.posted_tweet_id) {
        toast({
          tone: "success",
          message: "X に投稿しました。",
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
      setBusy(false);
    }
  }

  async function runPending() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.action === "cancel") {
        await api.cancelDraft(pending.draft.id);
        toast({ tone: "info", message: "取消しました（ゴミ箱）。復元できます。" });
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

  function openSchedule(draft: Draft) {
    // 既定値は最有力スロット(夜21時)の次の発生
    const best = rec?.slots.find((s) => s.tier === "best") ?? rec?.slots[0];
    setSchedAt(jstSlotValue(best ? best.hour : 21));
    setSchedDraft(draft);
  }

  function confirmSchedule() {
    if (!schedDraft || !schedAt) return;
    // 予約時刻が制限時間帯なら二段階確認を通し、override付きで予約する。
    gate((override) => doSchedule(override), toScheduledAtISO(schedAt));
  }

  async function doSchedule(override: boolean) {
    if (!schedDraft || !schedAt) return;
    setBusy(true);
    setError(null);
    try {
      await api.queue(schedDraft.id, "time", toScheduledAtISO(schedAt), override);
      toast({ tone: "info", message: `JST ${schedAt.replace("T", " ")} に予約しました。` });
      setSchedDraft(null);
      reload();
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `失敗しました: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  const total = pending ? pending.draft.segments.reduce((a, s) => a + charCount(s), 0) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Queue</h1>
        <p className="mt-1 text-sm text-zinc-500">自分の投稿の下書き・予約を管理します。</p>
      </div>

      {/* おすすめ投稿時間(JST) */}
      {rec && (
        <Card className="space-y-2">
          <div className="text-sm text-zinc-300">おすすめ投稿時間（JST）</div>
          <div className="flex flex-wrap gap-2">
            {rec.slots.map((s) => (
              <Badge key={s.hour} tone={TIER_TONE[s.tier]}>
                {s.hour}時 — {s.label}
              </Badge>
            ))}
          </div>
          <div className="text-xs text-zinc-500">{rec.note}</div>
          <div className="text-[11px] text-zinc-600">出典: {rec.sources.join(" / ")}</div>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={status === t.key ? "default" : "outline"}
            onClick={() => setStatus(t.key)}
          >
            {t.label}
            {t.key === "queued" && queuedCount > 0 && (
              <span className="ml-1.5 rounded-full bg-violet-600/30 px-1.5 text-xs text-violet-200">
                {queuedCount}
              </span>
            )}
          </Button>
        ))}
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {status === "queued" && drafts.length > 0 && (
        <div className="text-sm text-zinc-400">予約 {drafts.length} 件（投稿時刻の早い順）</div>
      )}
      {drafts.length === 0 && (
        <div className="text-sm text-zinc-500">
          {status === "queued" ? "予約はありません。" : "該当なし。"}
        </div>
      )}

      <div className="space-y-3">
        {drafts.map((d) => (
          <DraftCard
            key={d.id}
            draft={d}
            onUpdated={reload}
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
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPending({ action: "cancel", draft: d })}
                    >
                      取消
                    </Button>
                  </>
                )}
                {(d.status === "approved" || d.status === "queued") && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setPending({ action: "optimal", draft: d })}
                    >
                      最適時間に予約
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => openSchedule(d)}>
                      時間指定で予約
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setPending({ action: "post", draft: d })}
                    >
                      今すぐ投稿
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPending({ action: "cancel", draft: d })}
                    >
                      取消
                    </Button>
                  </>
                )}
                {d.status === "canceled" && (
                  <Button size="sm" variant="outline" onClick={() => act(() => api.restoreDraft(d.id))}>
                    下書きに戻す
                  </Button>
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

      {/* 投稿/最適予約の確認 */}
      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.action === "post"
            ? "本番アカウントへ投稿します"
            : pending?.action === "cancel"
              ? "この投稿を取消します"
              : "最適時間に予約します"
        }
        description={
          pending?.action === "post"
            ? `投稿先: @${me?.username ?? "(不明)"} ・ この操作は取り消せません。`
            : pending?.action === "cancel"
              ? pending.draft.status === "queued"
                ? "予約を解除してゴミ箱へ移動します（自動投稿はされません）。あとで「取消（ゴミ箱）」タブから復元できます。"
                : "ゴミ箱へ移動します。あとで「取消（ゴミ箱）」タブから復元できます。"
              : `投稿先: @${me?.username ?? "(不明)"} ・ 予約時刻に自動投稿されます。`
        }
        confirmLabel={
          pending?.action === "post" ? "投稿する" : pending?.action === "cancel" ? "取消する" : "予約する"
        }
        confirmVariant={pending?.action === "post" || pending?.action === "cancel" ? "danger" : "default"}
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      >
        {pending && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <Badge tone="sky">投稿</Badge>
              <span className="text-zinc-500">
                文字数合計 {total} / セグメント {pending.draft.segments.length}
                {pending.draft.media_paths.length > 0 && ` / メディア ${pending.draft.media_paths.length}`}
              </span>
            </div>
            {pending.draft.segments.map((s, i) => (
              <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
                <span className="whitespace-pre-wrap break-words">{s}</span>
              </div>
            ))}
          </div>
        )}
      </ConfirmDialog>

      {/* 時間指定予約(JST) */}
      <ConfirmDialog
        open={schedDraft !== null}
        title="時間を指定して予約（日本時間）"
        description={`投稿先: @${me?.username ?? "(不明)"} ・ 指定したJST時刻に自動投稿されます。`}
        confirmLabel="この時刻で予約"
        confirmVariant="default"
        busy={busy}
        onConfirm={confirmSchedule}
        onCancel={() => setSchedDraft(null)}
      >
        <SchedulePicker value={schedAt} onChange={setSchedAt} recommended={rec?.slots ?? []} />
      </ConfirmDialog>

      {blackoutGate}
    </div>
  );
}
