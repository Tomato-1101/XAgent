import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Input, Spinner, Switch } from "../components/ui";
import { useToast } from "../components/Toast";
import type { BlackoutSettings, MonitorSettings } from "../types";

const TOGGLES: { key: keyof MonitorSettings; label: string; hint: string }[] = [
  { key: "mentions_enabled", label: "メンション監視", hint: "自分宛の返信案を生成（既定オフ・手動返信推奨）" },
  { key: "manual_targets_enabled", label: "手動リスト監視", hint: "Targetsに追加した相手の新規投稿を絡み案に" },
  { key: "keyword_search_enabled", label: "キーワード/ジャンル検索", hint: "ジャンル対象の検索から絡み案（コスト増）" },
  { key: "following_enabled", label: "フォロー中の監視", hint: "フォロー中の新規投稿を絡み案に（コスト高・既定オフ）" },
];

const WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]; // index = Python weekday()

export default function Settings() {
  const [settings, setSettings] = useState<MonitorSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [bo, setBo] = useState<BlackoutSettings | null>(null);
  const [boSaving, setBoSaving] = useState(false);
  const [celebRunning, setCelebRunning] = useState(false);
  const [celebStatus, setCelebStatus] = useState<string | null>(null);
  const [buzzRunning, setBuzzRunning] = useState(false);
  const [buzzStatus, setBuzzStatus] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.getMonitorSettings().then(setSettings).catch((e) => setError(String(e)));
    api.getBlackout().then(setBo).catch((e) => setError(String(e)));
  }, []);

  async function toggle(key: keyof MonitorSettings, value: boolean) {
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSettings({ ...settings, [key]: value });
    try {
      setSettings(await api.putMonitorSettings({ [key]: value }));
    } catch (e) {
      setError(String(e));
      api.getMonitorSettings().then(setSettings).catch(() => {});
    } finally {
      setSaving(false);
    }
  }

  async function saveNum(key: "max_drafts_per_run" | "min_impressions" | "buzz_min_faves", n: number) {
    if (!settings) return;
    const val = Math.max(0, Math.floor(n) || 0);
    setSaving(true);
    setError(null);
    try {
      setSettings(await api.putMonitorSettings({ [key]: val }));
    } catch (e) {
      setError(String(e));
      api.getMonitorSettings().then(setSettings).catch(() => {});
    } finally {
      setSaving(false);
    }
  }

  async function runCelebOnce() {
    setCelebRunning(true);
    setCelebStatus("開始しています…");
    try {
      const { job_id } = await api.celebRunOnceStart();
      const res = await api.pollJob<{
        candidates: number;
        reply_suggestions: number;
        quote_suggestions: number;
        error?: string;
      }>(job_id, (msg, sec) => {
        const m = Math.floor(sec / 60);
        setCelebStatus(`${msg || "実行中"}（経過 ${m > 0 ? `${m}分` : ""}${sec % 60}秒）`);
      });
      if (res.error) {
        toast({ tone: "error", message: res.error });
      } else if (res.candidates === 0) {
        toast({ tone: "info", message: "有名人の新しいAI言及投稿はありませんでした。" });
      } else {
        const total = res.reply_suggestions + res.quote_suggestions;
        toast({
          tone: "success",
          message: total
            ? `絡み案を${total}件生成しました（Inboxで確認できます）。`
            : `候補${res.candidates}件を確認しましたが、絡む価値が高い投稿はありませんでした。`,
        });
      }
    } catch (e) {
      toast({ tone: "error", message: `有名人ウォッチに失敗: ${String(e)}` });
    } finally {
      setCelebRunning(false);
      setCelebStatus(null);
    }
  }

  async function runBuzzOnce() {
    setBuzzRunning(true);
    setBuzzStatus("開始しています…");
    try {
      const { job_id } = await api.buzzRunOnceStart();
      const res = await api.pollJob<{
        candidates: number;
        reply_suggestions: number;
        quote_suggestions: number;
      }>(job_id, (msg, sec) => {
        const m = Math.floor(sec / 60);
        setBuzzStatus(`${msg || "実行中"}（経過 ${m > 0 ? `${m}分` : ""}${sec % 60}秒）`);
      });
      if (res.candidates === 0) {
        toast({ tone: "info", message: "新しいバズ投稿はありませんでした。" });
      } else {
        const total = res.reply_suggestions + res.quote_suggestions;
        toast({
          tone: "success",
          message: total
            ? `絡み案を${total}件生成しました（Inboxで確認できます）。`
            : `候補${res.candidates}件を確認しましたが、絡む価値が高い投稿はありませんでした。`,
        });
      }
    } catch (e) {
      toast({ tone: "error", message: `バズウォッチに失敗: ${String(e)}` });
    } finally {
      setBuzzRunning(false);
      setBuzzStatus(null);
    }
  }

  function toggleWeekday(d: number) {
    if (!bo) return;
    const has = bo.weekdays.includes(d);
    const weekdays = has ? bo.weekdays.filter((x) => x !== d) : [...bo.weekdays, d].sort((a, b) => a - b);
    setBo({ ...bo, weekdays });
  }

  function setWindow(i: number, idx: 0 | 1, value: string) {
    if (!bo) return;
    const windows = bo.windows.map((w, j) => (j === i ? ((idx === 0 ? [value, w[1]] : [w[0], value]) as [string, string]) : w));
    setBo({ ...bo, windows });
  }

  function addWindow() {
    if (!bo) return;
    setBo({ ...bo, windows: [...bo.windows, ["09:00", "12:00"]] });
  }

  function removeWindow(i: number) {
    if (!bo) return;
    setBo({ ...bo, windows: bo.windows.filter((_, j) => j !== i) });
  }

  async function saveBlackout() {
    if (!bo) return;
    setBoSaving(true);
    setError(null);
    try {
      setBo(await api.putBlackout({ enabled: bo.enabled, weekdays: bo.weekdays, windows: bo.windows }));
      toast({ tone: "success", message: "制限時間帯を保存しました。" });
    } catch (e) {
      setError(String(e));
      toast({ tone: "error", message: `保存に失敗: ${String(e)}` });
    } finally {
      setBoSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-zinc-500">
          監視ソースのオン/オフと、自分の投稿/リポストを止める制限時間帯を設定します。
        </p>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">監視ソースのトグル</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          <>
            {TOGGLES.map((t) => (
              <Switch
                key={t.key}
                label={t.label}
                hint={t.hint}
                checked={Boolean(settings[t.key])}
                disabled={saving}
                onChange={(v) => toggle(t.key, v)}
              />
            ))}
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-zinc-800 pt-3">
              <span className="text-sm">
                <span className="text-zinc-200">1サイクルの最大生成数</span>
                <span className="ml-2 text-xs text-zinc-500">
                  監視1回で作る下書きの上限。多すぎる生成でAPIを圧迫しないための安全弁。
                </span>
              </span>
              <Input
                type="number"
                min={0}
                className="w-24 shrink-0"
                defaultValue={settings.max_drafts_per_run}
                disabled={saving}
                onBlur={(e) => saveNum("max_drafts_per_run", Number(e.target.value))}
              />
            </div>
            <div className="flex items-center justify-between gap-3 pt-2">
              <span className="text-sm">
                <span className="text-zinc-200">最低インプレッション</span>
                <span className="ml-2 text-xs text-zinc-500">
                  これ未満の閲覧数の投稿には絡まない（伸びていない投稿へのリプは露出が取れないため）。有名人ウォッチは対象外。
                </span>
              </span>
              <Input
                type="number"
                min={0}
                step={1000}
                className="w-28 shrink-0"
                defaultValue={settings.min_impressions}
                disabled={saving}
                onBlur={(e) => saveNum("min_impressions", Number(e.target.value))}
              />
            </div>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      {/* 絡み案の自動生成のオン/オフ(既定オフ)。予約投稿の発火はこれと無関係に常時。 */}
      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">絡み案の自動生成</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          <>
            <Switch
              label="絡み案を自動生成する"
              hint="オンの間だけ、上の監視ソースを定期巡回して返信案を自動で下書き生成する（既定オフ・下書きのみ・自動投稿はしない）"
              checked={Boolean(settings.auto_monitor_enabled)}
              disabled={saving}
              onChange={(v) => toggle("auto_monitor_enabled", v)}
            />
            <p className="pt-1 text-xs text-zinc-500">
              既定はオフ。オフの間はAPIを消費しません。生成したい時だけオンにしてください
              （<span className="text-zinc-300">オンのままにすると一定間隔で下書きが増え続けます</span>。終わったらオフに）。
              手動で1回だけ生成するなら Inbox の「監視を1回実行」。
              予約投稿の発火はこのスイッチと無関係に常時動きます（全投稿の緊急停止はサーバ側の posting_enabled）。
            </p>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      {/* 有名人ウォッチ: リストの有名人がAIについて投稿したら即絡み案(検索ベースの軽量検出) */}
      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">有名人ウォッチ（AI言及の即時検出）</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          <>
            <Switch
              label="有名人のAI投稿を自動検出する"
              hint="リストの有名人がAIについて投稿したら数分以内に絡み案を下書き生成（既定オフ・下書きのみ・自動投稿はしない）"
              checked={Boolean(settings.celeb_watch_enabled)}
              disabled={saving}
              onChange={(v) => toggle("celeb_watch_enabled", v)}
            />
            <p className="pt-1 text-xs text-zinc-500">
              検出は検索クエリ1〜2回/サイクルの軽量方式（タイムライン巡回なし）。AIに言及した投稿が
              見つかった時だけ生成AIが動きます。有名人はインプレッション下限の対象外（投稿直後に反応するため）。
              対象リスト: {settings.celeb_list_id ? (
                <span className="text-zinc-300">設定済み（ID: {settings.celeb_list_id}）</span>
              ) : (
                <span className="text-amber-400">未設定（Xリスト「有名人ウォッチ」を作成して設定してください）</span>
              )}
            </p>
            <div className="flex items-center gap-3 pt-2">
              <Button size="sm" variant="outline" onClick={runCelebOnce} disabled={celebRunning || !settings.celeb_list_id}>
                {celebRunning ? <Spinner /> : "今すぐチェック"}
              </Button>
              {celebStatus && <span className="text-xs text-zinc-400">{celebStatus}</span>}
            </div>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      {/* バズウォッチ: アカウント不問でmin_faves検索の「既にバズった投稿」に絡み案 */}
      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">バズウォッチ（バズ投稿の網羅検出）</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          <>
            <Switch
              label="バズ投稿を自動検出する"
              hint="アカウントを問わず「いいねがしきい値以上付いた投稿」を定期検索し、絡み案を下書き生成（既定オフ・下書きのみ・自動投稿はしない）"
              checked={Boolean(settings.buzz_watch_enabled)}
              disabled={saving}
              onChange={(v) => toggle("buzz_watch_enabled", v)}
            />
            <div className="flex items-center justify-between gap-3 pt-2">
              <span className="text-sm">
                <span className="text-zinc-200">バズ判定のいいね数</span>
                <span className="ml-2 text-xs text-zinc-500">
                  この数以上いいねが付いた日本語投稿を「バズ」として拾う（バズ予測ではなく実績で判定）。
                </span>
              </span>
              <Input
                type="number"
                min={0}
                step={500}
                className="w-28 shrink-0"
                defaultValue={settings.buzz_min_faves}
                disabled={saving}
                onBlur={(e) => saveNum("buzz_min_faves", Number(e.target.value))}
              />
            </div>
            <p className="pt-1 text-xs text-zinc-500">
              検出は検索クエリ1回/サイクル。どれに絡むかはAIが選定し（最大3件/回）、
              告知やファンダム向け投稿などリプが浮くものは外します。
              自動検出中は承認待ちの絡み案が30件以上溜まると生成を一時停止します（承認かInboxの整理で再開）。
            </p>
            <div className="flex items-center gap-3 pt-2">
              <Button size="sm" variant="outline" onClick={runBuzzOnce} disabled={buzzRunning}>
                {buzzRunning ? <Spinner /> : "今すぐチェック"}
              </Button>
              {buzzStatus && <span className="text-xs text-zinc-400">{buzzStatus}</span>}
            </div>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      {/* 制限時間帯(ブラックアウト) */}
      <Card className="space-y-4">
        <div>
          <div className="text-sm text-zinc-200">制限時間帯（自分の投稿・リポストを止める）</div>
          <div className="mt-1 text-xs text-zinc-500">
            指定曜日のこの時間帯（JST）は投稿・リポストをブロックします。土日は既定で対象外。
            監視（読み取り）は止めません。どうしても投稿する場合は警告→最終確認の二段階で実行できます。
          </div>
        </div>

        {bo ? (
          <>
            <Switch
              label="制限を有効にする"
              checked={bo.enabled}
              onChange={(v) => setBo({ ...bo, enabled: v })}
            />

            <div className="space-y-1">
              <div className="text-xs text-zinc-500">対象の曜日</div>
              <div className="flex flex-wrap gap-1.5">
                {WEEKDAYS.map((w, d) => {
                  const on = bo.weekdays.includes(d);
                  return (
                    <button
                      key={d}
                      onClick={() => toggleWeekday(d)}
                      className={
                        "h-8 w-9 rounded-md border text-sm " +
                        (on
                          ? "border-sky-600 bg-sky-600/20 text-sky-200"
                          : "border-zinc-700 text-zinc-400 hover:bg-zinc-800")
                      }
                    >
                      {w}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-xs text-zinc-500">時間帯（その日の制限帯。複数可）</div>
              {bo.windows.map((w, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <Input
                    type="time"
                    value={w[0]}
                    onChange={(e) => setWindow(i, 0, e.target.value)}
                    className="w-28"
                  />
                  <span className="text-zinc-500">〜</span>
                  <Input
                    type="time"
                    value={w[1]}
                    onChange={(e) => setWindow(i, 1, e.target.value)}
                    className="w-28"
                  />
                  <Button size="sm" variant="ghost" onClick={() => removeWindow(i)}>
                    削除
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="outline" onClick={addWindow}>
                時間帯を追加
              </Button>
            </div>

            <div>
              <Button onClick={saveBlackout} disabled={boSaving}>
                {boSaving ? <Spinner /> : "制限時間帯を保存"}
              </Button>
            </div>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      <p className="text-xs text-zinc-600">
        監視でオンにしたソースだけが、自動生成オン時の巡回や「Inbox の監視を1回実行」で巡回されます。
        投稿は監視で自動化されず、承認したときだけ送信されます。
      </p>
    </div>
  );
}
