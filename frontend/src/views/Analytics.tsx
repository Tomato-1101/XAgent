import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Input, Spinner, Switch } from "../components/ui";
import { useToast } from "../components/Toast";
import type {
  CostGroup,
  CostResponse,
  MetricsAgg,
  MetricsSettings,
  MetricsSummary,
  SummaryResponse,
} from "../types";

/** コスト種別の表示名。 */
const KIND_LABEL: Record<string, string> = {
  read: "読み取り",
  write: "投稿",
  tl: "タイムライン取得",
  llm: "整形・解析(トークン)",
};

/** 自分の投稿の種別表示名（メトリクス集計用）。 */
const POST_KIND_LABEL: Record<string, string> = {
  post: "オリジナル",
  reply: "リプライ",
  quote: "引用RT",
  repost: "リポスト",
};

/** 数値を 1,234 形式に。null/未取得は「—」。 */
function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Math.round(n).toLocaleString();
}

/** メトリクス集計の1行（種別別／合計）。 */
function MetricRow({ label, agg, bold }: { label: string; agg: MetricsAgg; bold?: boolean }) {
  const cell = "px-2 py-1.5 text-right tabular-nums";
  return (
    <tr className={bold ? "border-t border-zinc-700 font-medium text-zinc-200" : "text-zinc-400"}>
      <td className="px-2 py-1.5 text-left text-zinc-300">{label}</td>
      <td className={cell}>{agg.count}</td>
      <td className={cell}>{fmtNum(agg.avg_impressions)}</td>
      <td className={cell}>{fmtNum(agg.median_impressions)}</td>
      <td className={cell}>{agg.avg_likes.toFixed(1)}</td>
      <td className={cell}>{agg.avg_retweets.toFixed(1)}</td>
      <td className={cell}>{agg.avg_replies.toFixed(1)}</td>
      <td className={cell}>{agg.avg_quotes.toFixed(1)}</td>
      <td className={cell}>{agg.avg_bookmarks.toFixed(2)}</td>
    </tr>
  );
}

function CostBreakdown({ group }: { group: CostGroup }) {
  const entries = Object.entries(group.by_kind);
  return (
    <div className="mt-3 space-y-1 text-xs text-zinc-500">
      {entries.length === 0 && <div>記録なし</div>}
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between">
          <span>{KIND_LABEL[k] ?? k}</span>
          <span>
            {v.units} {k === "llm" ? "トークン" : "件"} / ${v.cost_usd.toFixed(3)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Analytics() {
  const [cost, setCost] = useState<CostResponse | null>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [mset, setMset] = useState<MetricsSettings | null>(null);
  const [metricsRunning, setMetricsRunning] = useState(false);
  const [metricsStatus, setMetricsStatus] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.cost().then(setCost).catch((e) => setError(String(e)));
    api.summary().then(setSummary).catch((e) => setError(String(e)));
    api.metricsSummary().then(setMetrics).catch((e) => setError(String(e)));
    api.getMetricsSettings().then(setMset).catch((e) => setError(String(e)));
  }, []);

  async function toggleMetrics(value: boolean) {
    if (!mset) return;
    setMset({ ...mset, metrics_enabled: value });
    try {
      setMset(await api.putMetricsSettings({ metrics_enabled: value }));
    } catch (e) {
      setError(String(e));
      api.getMetricsSettings().then(setMset).catch(() => {});
    }
  }

  async function saveLookback(n: number) {
    if (!mset) return;
    try {
      setMset(await api.putMetricsSettings({ lookback_days: Math.max(1, Math.floor(n) || 1) }));
    } catch (e) {
      setError(String(e));
      api.getMetricsSettings().then(setMset).catch(() => {});
    }
  }

  async function runMetricsOnce() {
    setMetricsRunning(true);
    setMetricsStatus("開始しています…");
    try {
      const { job_id } = await api.metricsRunOnceStart();
      const res = await api.pollJob<{ fetched: number; inserted: number; updated: number }>(
        job_id,
        (msg, sec) => {
          const m = Math.floor(sec / 60);
          setMetricsStatus(`${msg || "取得中"}（経過 ${m > 0 ? `${m}分` : ""}${sec % 60}秒）`);
        },
      );
      toast({
        tone: "success",
        message: `自分の投稿メトリクスを${res.fetched}件取得しました（新規${res.inserted}・更新${res.updated}）。`,
      });
      api.metricsSummary().then(setMetrics).catch(() => {});
    } catch (e) {
      toast({ tone: "error", message: `メトリクス取得に失敗: ${String(e)}` });
    } finally {
      setMetricsRunning(false);
      setMetricsStatus(null);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Analytics</h1>
        <p className="mt-1 text-sm text-zinc-500">
          API 従量課金のコスト（X API と Claude API を分けて表示）と下書きの状況。
        </p>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      {/* 合計 */}
      <Card>
        <div className="text-sm text-zinc-400">累計 API コスト（合計）</div>
        <div className="mt-1 text-3xl font-semibold">${cost?.total_usd ?? 0}</div>
        <div className="mt-1 text-xs text-zinc-500">X API + Claude API の合算</div>
      </Card>

      {/* X / Claude 内訳 */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <div className="text-sm text-zinc-400">X API</div>
          <div className="mt-1 text-2xl font-semibold">${cost?.x_api.cost_usd ?? 0}</div>
          {cost && <CostBreakdown group={cost.x_api} />}
        </Card>

        <Card>
          <div className="text-sm text-zinc-400">Claude API（クラウド）</div>
          <div className="mt-1 text-2xl font-semibold">${cost?.claude_api.cost_usd ?? 0}</div>
          {cost && <CostBreakdown group={cost.claude_api} />}
        </Card>
      </div>

      {/* 投稿パフォーマンス(B): 自分の投稿の種別別の平均インプ/エンゲージ。改善を数字で追う。 */}
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm text-zinc-400">投稿パフォーマンス（種別別）</div>
            <div className="mt-0.5 text-xs text-zinc-500">
              自分の投稿の平均インプレッション・いいね・RT・リプ・引用・ブックマーク。
              {metrics?.captured_at ? (
                <span className="ml-1">最終取得: {new Date(metrics.captured_at + "Z").toLocaleString()}</span>
              ) : (
                <span className="ml-1 text-amber-400">未取得（「今すぐ取得」で実データを読み込みます）</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            {metricsStatus && <span className="text-xs text-zinc-400">{metricsStatus}</span>}
            <Button size="sm" variant="outline" onClick={runMetricsOnce} disabled={metricsRunning}>
              {metricsRunning ? <Spinner /> : "今すぐ取得"}
            </Button>
          </div>
        </div>

        {metrics && metrics.total.count > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-xs">
              <thead>
                <tr className="text-zinc-500">
                  <th className="px-2 py-1 text-left font-normal">種別</th>
                  <th className="px-2 py-1 text-right font-normal">件数</th>
                  <th className="px-2 py-1 text-right font-normal">平均インプ</th>
                  <th className="px-2 py-1 text-right font-normal">中央インプ</th>
                  <th className="px-2 py-1 text-right font-normal">いいね</th>
                  <th className="px-2 py-1 text-right font-normal">RT</th>
                  <th className="px-2 py-1 text-right font-normal">リプ</th>
                  <th className="px-2 py-1 text-right font-normal">引用</th>
                  <th className="px-2 py-1 text-right font-normal">BM</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(metrics.by_kind).map(([k, agg]) => (
                  <MetricRow key={k} label={POST_KIND_LABEL[k] ?? k} agg={agg} />
                ))}
                <MetricRow label="合計" agg={metrics.total} bold />
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-sm text-zinc-500">
            まだメトリクスがありません。「今すぐ取得」で直近{mset?.lookback_days ?? 30}日分の自分の投稿を読み込みます（読み取りのみ・投稿しません）。
          </div>
        )}

        {mset && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 pt-3">
            <Switch
              label="自動取得する"
              hint="一定間隔で自分の投稿の実績を取得（読み取りのみ・投稿しない・既定オン）"
              checked={Boolean(mset.metrics_enabled)}
              onChange={toggleMetrics}
            />
            <span className="flex items-center gap-2 text-sm">
              <span className="text-xs text-zinc-500">取得対象（日数）</span>
              <Input
                type="number"
                min={1}
                className="w-20 shrink-0"
                defaultValue={mset.lookback_days}
                onBlur={(e) => saveLookback(Number(e.target.value))}
              />
            </span>
          </div>
        )}
      </Card>

      <Card>
        <div className="text-sm text-zinc-400">下書きの状況</div>
        <div className="mt-3 space-y-1 text-sm">
          {summary &&
            Object.entries(summary.draft_counts).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-zinc-400">{k}</span>
                <span className="font-medium">{v}</span>
              </div>
            ))}
        </div>
      </Card>
    </div>
  );
}
