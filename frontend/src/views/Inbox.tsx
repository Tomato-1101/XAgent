import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Input, Spinner } from "../components/ui";
import { DraftCard, charCount } from "../components/DraftCard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { useBlackoutGate } from "../components/BlackoutGate";
import { useAutoRefresh } from "../components/useAutoRefresh";
import { AgentHint } from "../components/AgentHint";
import type { AgentPhrase } from "../components/AgentHint";
import type { Draft, Me } from "../types";

const AGENT_PHRASES: AgentPhrase[] = [
  {
    say: "このURLにこう絡んで",
    does: "指定ツイートに自分の文でリプライ/引用する下書き(元ポスト本文付き)",
    cmd: 'xagent reply <URL> "<返信文>"',
  },
  {
    say: "絡み案を5件だけ生成して",
    does: "1サイクルの生成数上限を5にして監視を1回実行(APIを圧迫しない)",
    cmd: "xagent monitor-config --max 5 && xagent monitor-once",
  },
];

function tweetUrl(me: Me | null, id: string): string {
  return me?.username ? `https://x.com/${me.username}/status/${id}` : `https://x.com/i/web/status/${id}`;
}

// 返信先(相手の元ツイート)を開くURL。返信はAPI送信できないので、ここを開いて手動返信する。
function replyTargetUrl(d: Draft): string {
  return d.target_handle
    ? `https://x.com/${d.target_handle}/status/${d.target_tweet_id}`
    : `https://x.com/i/web/status/${d.target_tweet_id}`;
}

const KIND_LABEL: Record<string, string> = { reply: "返信", quote: "引用RT", post: "投稿" };

// ジョブ進捗の表示文(「何をしているか（経過 N分M秒）」)。待ち時間とエラーを区別できるようにする。
function progressLabel(message: string, elapsedSeconds: number): string {
  const m = Math.floor(elapsedSeconds / 60);
  const s = elapsedSeconds % 60;
  const elapsed = m > 0 ? `${m}分${s}秒` : `${s}秒`;
  return `${message || "実行中"}（経過 ${elapsed}）`;
}

export default function Inbox({ me }: { me: Me | null }) {
  const [items, setItems] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [runLimit, setRunLimit] = useState(10); // 1回実行で作る上限(乱造防止・既定10)
  const [runProgress, setRunProgress] = useState<string | null>(null); // 監視1回実行の進捗
  const [quoteUrl, setQuoteUrl] = useState(""); // 手動で引用案を作る対象ツイートURL
  const [quoting, setQuoting] = useState(false);
  const [quoteProgress, setQuoteProgress] = useState<string | null>(null); // 引用案生成の進捗
  const [pending, setPending] = useState<Draft | null>(null);
  const [recastingId, setRecastingId] = useState<number | null>(null); // 型切替中の案id
  const [regeneratingId, setRegeneratingId] = useState<number | null>(null); // 再生成中の案id
  const [actionProgress, setActionProgress] = useState<string | null>(null); // 型切替/再生成の進捗
  const toast = useToast();
  const { gate, element: blackoutGate } = useBlackoutGate();

  function reload() {
    api
      .listDrafts("draft")
      .then((all) => setItems(all.filter((d) => d.kind !== "post")))
      .catch((e) => setError(String(e)));
  }

  useEffect(reload, []);
  // スマホでタブ復帰した時や生成完了後に手動リロードしなくても一覧が追従するように
  useAutoRefresh(reload);

  // 実行中の監視ジョブを完了まで追跡し、進捗表示と結果反映を行う(開始直後・画面再訪の両方から呼ぶ)
  async function trackMonitorJob(jobId: string) {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await api.pollJob<{
        reply_suggestions: number;
        quote_suggestions: number;
        candidates: number;
      }>(jobId, (msg, sec) => {
        setRunProgress(progressLabel(msg, sec));
        // 下書きは選定後1件ずつDBに入るので、作成が始まったら一覧へ即反映する
        if (msg.includes("本文作成中") || msg.startsWith("下書き作成中")) reload();
      });
      setInfo(
        r.candidates === 0
          ? "新しい絡み候補が見つかりませんでした（24時間以内の対象投稿がない、または生成済み・却下済み）。"
          : `返信案 ${r.reply_suggestions} 件・引用案 ${r.quote_suggestions} 件を生成しました（候補 ${r.candidates} 件）`
      );
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setRunProgress(null);
    }
  }

  async function runMonitor() {
    setBusy(true);
    setError(null);
    setInfo(null);
    setRunProgress("開始しています…");
    try {
      const { job_id } = await api.monitorRunOnceStart(runLimit > 0 ? runLimit : undefined);
      await trackMonitorJob(job_id);
    } catch (e) {
      setError(String(e));
      setBusy(false);
      setRunProgress(null);
    }
  }

  // 画面を開いた時、進行中の監視ジョブ(他端末やCLIから開始した分も含む)があれば再接続して
  // 進捗を表示する。リロードや画面遷移で進捗が見えなくなる問題への対策。
  useEffect(() => {
    api
      .runningJobs()
      .then((jobs) => {
        const j = jobs.find((x) => x.label === "monitor-run-once");
        if (j) {
          setRunProgress(progressLabel(j.progress ?? "実行中の生成に再接続しました", j.elapsed_seconds));
          void trackMonitorJob(j.job_id);
        }
      })
      .catch(() => {/* 一覧が取れなくても画面表示は続行 */});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // URLから引用案(引用RT)をAIに生成させる。監視の自動生成と同じく相手投稿からコメントをAI生成。
  async function makeQuoteFromUrl() {
    const url = quoteUrl.trim();
    if (!url) return;
    setQuoting(true);
    setError(null);
    setInfo(null);
    setQuoteProgress("開始しています…");
    try {
      await api.quoteFromUrl(url, (msg, sec) => setQuoteProgress(progressLabel(msg, sec)));
      setQuoteUrl("");
      setInfo("引用案を生成しました。下の「引用案」に表示されます。");
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setQuoting(false);
      setQuoteProgress(null);
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

  // AIが選んだ型を人が上書きする。リプライ⇄引用RTで本文を作り直し、もう片方のセクションへ移す。
  async function recast(d: Draft, to: "reply" | "quote") {
    setRecastingId(d.id);
    setError(null);
    setActionProgress(progressLabel("開始しています…", 0));
    try {
      await api.recast(d.id, to, (msg, sec) => setActionProgress(progressLabel(msg, sec)));
      toast({
        tone: "success",
        message: to === "quote" ? "引用RT案に切り替えました。" : "リプライ案に切り替えました。",
      });
      reload();
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `切替に失敗しました: ${String(e)}` });
    } finally {
      setRecastingId(null);
      setActionProgress(null);
    }
  }

  // 同じ型のまま本文だけウェブ検索つきでAIに作り直させる(型は変えない。型切替は recast)。
  async function regenerate(d: Draft) {
    setRegeneratingId(d.id);
    setError(null);
    setActionProgress(progressLabel("開始しています…", 0));
    try {
      await api.regenerateDraft(d.id, (msg, sec) => setActionProgress(progressLabel(msg, sec)));
      toast({ tone: "success", message: "リプ案を再生成しました。" });
      reload();
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `再生成に失敗しました: ${String(e)}` });
    } finally {
      setRegeneratingId(null);
      setActionProgress(null);
    }
  }

  // 返信はAPI送信できないので「本文をコピー」+「元ポストを別タブで開く」を1操作にまとめる。
  // ポップアップブロック回避のため、クリック同期内で先にタブを開いてからコピーする。
  function copyAndOpen(d: Draft) {
    const text = d.segments.join("\n");
    window.open(replyTargetUrl(d), "_blank", "noopener");
    navigator.clipboard
      .writeText(text)
      .then(() =>
        toast({
          tone: "success",
          message: "返信文をコピーし、元ポストを開きました。返信欄に貼り付けて投稿してください。",
        })
      )
      .catch(() =>
        toast({ tone: "error", message: "コピーに失敗しました。本文を手動で選択してください。" })
      );
  }

  // 「送信する」→ 制限帯ゲート(警告→最終確認)を通してから即時送信
  function confirmSend() {
    if (!pending) return;
    const draft = pending;
    gate((override) => doSend(draft, override));
  }

  async function doSend(draft: Draft, override: boolean) {
    setSending(true);
    setError(null);
    try {
      await api.approve(draft.id);
      const posted = await api.postNow(draft.id, override);
      if (posted.posted_tweet_id) {
        toast({
          tone: "success",
          message: `${KIND_LABEL[draft.kind] ?? "投稿"}を送信しました。`,
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

  const total = pending ? pending.segments.reduce((a, s) => a + charCount(s), 0) : 0;

  // 返信案と引用案は性質が違う(返信=会話 / 引用RT=拡散)ので分けて表示する。
  const replies = items.filter((d) => d.kind === "reply");
  const quotes = items.filter((d) => d.kind === "quote");

  function renderCard(d: Draft) {
    // 返信はX APIの制限(未engage会話への返信=403)で送信できない → 手動送信導線にする。
    const isReply = d.kind === "reply";
    return (
      <DraftCard
        key={d.id}
        draft={d}
        onUpdated={() => {
          toast({ tone: "success", message: "案を更新しました。" });
          reload();
        }}
        actions={
          isReply ? (
            <>
              <Button size="sm" variant="danger" onClick={() => copyAndOpen(d)}>
                コピーして元ポストを開く
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={recastingId === d.id || regeneratingId === d.id}
                onClick={() => regenerate(d)}
              >
                {regeneratingId === d.id ? <Spinner /> : "リプ案を再生成"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={recastingId === d.id || regeneratingId === d.id}
                onClick={() => recast(d, "quote")}
              >
                {recastingId === d.id ? <Spinner /> : "引用RTで送る"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => act(() => api.reject(d.id))}>
                却下
              </Button>
              {(recastingId === d.id || regeneratingId === d.id) && actionProgress && (
                <span className="flex w-full items-center gap-1 text-xs text-amber-300/90">
                  <Spinner /> {actionProgress}
                </span>
              )}
            </>
          ) : (
            <>
              <Button size="sm" variant="danger" onClick={() => setPending(d)}>
                承認して送信
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  act(async () => {
                    await api.approve(d.id);
                    toast({
                      tone: "success",
                      message: "承認しました。Queueの「承認済み」タブで予約・投稿できます。",
                    });
                  })
                }
              >
                承認のみ
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={recastingId === d.id || regeneratingId === d.id}
                onClick={() => regenerate(d)}
              >
                {regeneratingId === d.id ? <Spinner /> : "引用案を再生成"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={recastingId === d.id || regeneratingId === d.id}
                onClick={() => recast(d, "reply")}
              >
                {recastingId === d.id ? <Spinner /> : "手動で返信に切替"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => act(() => api.reject(d.id))}>
                却下
              </Button>
              {(recastingId === d.id || regeneratingId === d.id) && actionProgress && (
                <span className="flex w-full items-center gap-1 text-xs text-amber-300/90">
                  <Spinner /> {actionProgress}
                </span>
              )}
            </>
          )
        }
      />
    );
  }

  function section(title: string, tone: "sky" | "violet", list: Draft[]) {
    if (list.length === 0) return null;
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{title}</Badge>
          <span className="text-xs text-zinc-500">{list.length} 件</span>
        </div>
        {list.map(renderCard)}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">Inbox</h1>
          <p className="mt-1 text-sm text-zinc-500">
            投稿ごとにAIが返信／引用RTの良い方を1案だけ作ります。型を変えたいときは各案のボタン（「引用RTで送る」「手動で返信に切替」）で切り替えられます。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-zinc-500">
            最大
            <Input
              type="number"
              min={1}
              className="w-16"
              value={runLimit}
              disabled={busy}
              onChange={(e) => setRunLimit(Math.max(1, Math.floor(Number(e.target.value) || 0)))}
            />
            件
          </label>
          <Button onClick={runMonitor} disabled={busy} variant="outline">
            {busy ? <Spinner /> : "監視を1回実行"}
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-2 sm:flex-row sm:items-center">
        <span className="shrink-0 text-xs text-zinc-500">URLから引用案を作る</span>
        <Input
          type="text"
          placeholder="https://x.com/.../status/..."
          className="flex-1"
          value={quoteUrl}
          disabled={quoting}
          onChange={(e) => setQuoteUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") makeQuoteFromUrl();
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={makeQuoteFromUrl}
          disabled={quoting || !quoteUrl.trim()}
        >
          {quoting ? <Spinner /> : "引用案を作る"}
        </Button>
      </div>

      <AgentHint title="絡みをClaude Codeに任せる" phrases={AGENT_PHRASES} />

      {busy && runProgress && (
        <div className="flex items-center gap-2 text-sm text-amber-300/90">
          <Spinner /> <span>{runProgress}</span>
        </div>
      )}
      {quoting && quoteProgress && (
        <div className="flex items-center gap-2 text-sm text-amber-300/90">
          <Spinner /> <span>{quoteProgress}</span>
        </div>
      )}
      {info && <div className="text-sm text-sky-300">{info}</div>}
      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {items.length === 0 && <div className="text-sm text-zinc-500">承認待ちの案はありません。</div>}

      <div className="space-y-6">
        {replies.length > 0 && (
          <p className="text-xs text-amber-300/90">
            返信はX側の制限でアプリから送信できません。「コピーして元ポストを開く」を押すと返信文をコピーして相手のポストを別タブで開くので、返信欄に貼り付けて投稿してください。
          </p>
        )}
        {section("返信案", "sky", replies)}
        {section("引用案（引用RT）", "violet", quotes)}
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
              <span className="text-zinc-500">文字数合計 {total}</span>
            </div>
            {pending.segments.map((s, i) => (
              <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
                <span className="whitespace-pre-wrap break-words">{s}</span>
              </div>
            ))}
          </div>
        )}
      </ConfirmDialog>

      {blackoutGate}
    </div>
  );
}
