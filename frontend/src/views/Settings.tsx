import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Input, Spinner, Switch } from "../components/ui";
import { useToast } from "../components/Toast";
import type { BlackoutSettings, MonitorSettings } from "../types";

const TOGGLES: { key: keyof MonitorSettings; label: string; hint: string }[] = [
  { key: "mentions_enabled", label: "メンション監視", hint: "自分宛の返信案を生成" },
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

  async function saveMax(n: number) {
    if (!settings) return;
    const val = Math.max(0, Math.floor(n) || 0);
    setSaving(true);
    setError(null);
    try {
      setSettings(await api.putMonitorSettings({ max_drafts_per_run: val }));
    } catch (e) {
      setError(String(e));
      api.getMonitorSettings().then(setSettings).catch(() => {});
    } finally {
      setSaving(false);
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
                onBlur={(e) => saveMax(Number(e.target.value))}
              />
            </div>
          </>
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      {/* 絡み案の自動生成のオン/オフ(常駐APIに統合済み) */}
      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">絡み案の自動生成</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          <>
            <Switch
              label="絡み案を自動生成する"
              hint="定期的に監視を回し、返信案・絡み案を自動で下書き生成する（下書きのみ・自動投稿はしない）"
              checked={Boolean(settings.auto_monitor_enabled)}
              disabled={saving}
              onChange={(v) => toggle("auto_monitor_enabled", v)}
            />
            <p className="pt-1 text-xs text-zinc-500">
              このスイッチは絡み案の自動生成だけを制御します。OFFにするとAPIを消費しません（生成したい時だけON）。
              予約投稿の発火は常時自動で動くため、ここでは止められません（全投稿の緊急停止はサーバ側の posting_enabled）。
              常駐APIに統合済みなので、別途デーモンを起動する必要はありません。
            </p>
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
                <div key={i} className="flex items-center gap-2">
                  <Input
                    type="time"
                    value={w[0]}
                    onChange={(e) => setWindow(i, 0, e.target.value)}
                    className="w-32"
                  />
                  <span className="text-zinc-500">〜</span>
                  <Input
                    type="time"
                    value={w[1]}
                    onChange={(e) => setWindow(i, 1, e.target.value)}
                    className="w-32"
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
        監視でオンにしたソースだけが「Inbox の監視を1回実行」や常駐で巡回されます。
        投稿は監視で自動化されず、承認したときだけ送信されます。
      </p>
    </div>
  );
}
