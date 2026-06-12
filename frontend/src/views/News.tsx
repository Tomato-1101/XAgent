import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input, Spinner, Switch } from "../components/ui";
import { useToast } from "../components/Toast";
import type { NewsItem, NewsSettings } from "../types";

// ジョブ進捗の表示文(「何をしているか（経過 N分M秒）」)。待ち時間とエラーを区別できるようにする。
function progressLabel(message: string, elapsedSeconds: number): string {
  const m = Math.floor(elapsedSeconds / 60);
  const s = elapsedSeconds % 60;
  const elapsed = m > 0 ? `${m}分${s}秒` : `${s}秒`;
  return `${message || "実行中"}（経過 ${elapsed}）`;
}

const SLOT_LABEL: Record<string, string> = { morning: "朝", evening: "夜" };

export default function News() {
  const [settings, setSettings] = useState<NewsSettings | null>(null);
  const [items, setItems] = useState<NewsItem[]>([]);
  const [lastDigestId, setLastDigestId] = useState(0);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [runProgress, setRunProgress] = useState<string | null>(null);
  const toast = useToast();

  function reload() {
    api
      .getNewsSettings()
      .then(setSettings)
      .catch((e) => setError(String(e)));
    api
      .newsRecent()
      .then((r) => {
        setItems(r.items);
        setLastDigestId(r.last_digest_id);
        setSourceError(r.available ? null : r.error);
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(reload, []);

  // 実行中の生成ジョブを完了まで追跡(開始直後・画面再訪の両方から呼ぶ)
  async function trackNewsJob(jobId: string) {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await api.pollJob<{ news_items: number; created: number }>(jobId, (msg, sec) =>
        setRunProgress(progressLabel(msg, sec))
      );
      setInfo(
        r.news_items === 0
          ? "新着ニュースがありません（前回の生成以降にXNewsBotの収集がまだ走っていません）。"
          : r.created === 0
            ? `新着 ${r.news_items} 件を確認しましたが、速報価値が高い記事がなく下書きは作りませんでした。`
            : `新着 ${r.news_items} 件から速報下書きを ${r.created} 件生成しました。Queueタブの下書きから承認できます。`
      );
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setRunProgress(null);
    }
  }

  async function runOnce() {
    setBusy(true);
    setError(null);
    setInfo(null);
    setRunProgress("開始しています…");
    try {
      const { job_id } = await api.newsRunOnceStart();
      await trackNewsJob(job_id);
    } catch (e) {
      setError(String(e));
      setBusy(false);
      setRunProgress(null);
    }
  }

  // 画面を開いた時、進行中の生成ジョブがあれば再接続して進捗を表示する
  useEffect(() => {
    api
      .runningJobs()
      .then((jobs) => {
        const j = jobs.find((x) => x.label === "news-run-once");
        if (j) {
          setRunProgress(progressLabel(j.progress ?? "実行中の生成に再接続しました", j.elapsed_seconds));
          void trackNewsJob(j.job_id);
        }
      })
      .catch(() => {/* 一覧が取れなくても画面表示は続行 */});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save(values: Partial<NewsSettings>) {
    setSaving(true);
    try {
      const s = await api.putNewsSettings(values);
      setSettings(s);
      toast({ tone: "success", message: "設定を保存しました。" });
    } catch (e) {
      toast({ tone: "error", message: String(e) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">News — ニュース速報の下書き生成</h1>
        <p className="text-sm text-zinc-400">
          XNewsBot が朝夕に収集したニュース（AI/テック）の新着から、大手アカウントの実投稿に学んだ
          速報の型（N1〜N5・Templatesタブで編集可）で投稿下書きを生成します。投稿は必ず人間の承認が必要です。
        </p>
      </div>

      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={runOnce} disabled={busy}>
            {busy ? <Spinner /> : "今すぐ生成"}
          </Button>
          <span className="text-xs text-zinc-500">
            前回の生成より後に届いた新着ダイジェストだけが対象です（同じニュースは二度生成しません）。
          </span>
        </div>
        {busy && runProgress && (
          <div className="flex items-center gap-2 text-sm text-amber-300/90">
            <Spinner /> <span>{runProgress}</span>
          </div>
        )}
        {info && <p className="text-sm text-emerald-300/90">{info}</p>}
        {error && <p className="text-sm text-red-400">{error}</p>}
      </Card>

      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold text-zinc-300">自動生成（ダイジェスト連動）</h2>
        {settings ? (
          <>
            <Switch
              label="新着ダイジェストから自動で下書きを生成する"
              hint="XNewsBotの朝夕の収集後、新着を検知して自動生成する（既定オフ・下書きのみ・自動投稿はしない）"
              checked={Boolean(settings.auto_news_enabled)}
              disabled={saving}
              onChange={(v) => save({ auto_news_enabled: v })}
            />
            <div className="flex items-center gap-2 text-sm">
              <span className="text-zinc-400">1回の生成上限</span>
              <Input
                type="number"
                min={1}
                max={10}
                className="w-20"
                value={settings.max_posts_per_run}
                disabled={saving}
                onChange={(e) =>
                  setSettings({ ...settings, max_posts_per_run: Number(e.target.value) })
                }
                onBlur={() => save({ max_posts_per_run: settings.max_posts_per_run })}
              />
              <span className="text-xs text-zinc-500">件（乱造防止の安全弁）</span>
            </div>
            <p className="text-xs text-zinc-500">
              オフの間は何もしません（新着確認はローカルDBを読むだけでAPIを消費しません）。
              生成された下書きは Queue タブに「[ニュース速報]」として入ります。
            </p>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <h2 className="text-sm font-semibold text-zinc-300">直近2日のニュース（対象ジャンル）</h2>
        {sourceError && (
          <p className="text-sm text-amber-300/90">
            XNewsBot のデータを読めません: {sourceError}
          </p>
        )}
        {!sourceError && items.length === 0 && (
          <p className="text-sm text-zinc-500">直近2日に対象ジャンルのニュースがありません。</p>
        )}
        <ul className="space-y-3">
          {items.map((n) => (
            <li key={`${n.digest_id}-${n.index}`} className="space-y-1 border-b border-zinc-800 pb-2 last:border-b-0">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Badge tone="sky">{n.genre}</Badge>
                <span className="text-zinc-500">
                  {n.digest_date} {SLOT_LABEL[n.slot] ?? n.slot}
                </span>
                {n.top_view_count > 0 && (
                  <span className="text-zinc-500">{n.top_view_count.toLocaleString()} views</span>
                )}
                {n.digest_id > lastDigestId && <Badge tone="amber">未生成</Badge>}
              </div>
              <div className="text-sm font-medium text-zinc-200">{n.title}</div>
              <div className="text-xs text-zinc-400">{n.summary}</div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
