import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Spinner, Switch } from "../components/ui";
import type { MonitorSettings } from "../types";

const TOGGLES: { key: keyof MonitorSettings; label: string; hint: string }[] = [
  { key: "mentions_enabled", label: "メンション監視", hint: "自分宛の返信案を生成" },
  { key: "manual_targets_enabled", label: "手動リスト監視", hint: "Targetsに追加した相手の新規投稿を絡み案に" },
  { key: "keyword_search_enabled", label: "キーワード/ジャンル検索", hint: "ジャンル対象の検索から絡み案（コスト増）" },
  { key: "following_enabled", label: "フォロー中の監視", hint: "フォロー中の新規投稿を絡み案に（コスト高・既定オフ）" },
];

export default function Settings() {
  const [settings, setSettings] = useState<MonitorSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getMonitorSettings().then(setSettings).catch((e) => setError(String(e)));
  }, []);

  async function toggle(key: keyof MonitorSettings, value: boolean) {
    if (!settings) return;
    setSaving(true);
    setError(null);
    // 楽観更新（即反映）。失敗時はサーバ値で戻す。
    setSettings({ ...settings, [key]: value });
    try {
      const updated = await api.putMonitorSettings({ [key]: value });
      setSettings(updated);
    } catch (e) {
      setError(String(e));
      api.getMonitorSettings().then(setSettings).catch(() => {});
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-zinc-500">
          絡みの監視ソースを個別にオン/オフできます。コストの高いソースは既定でオフです。
        </p>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      <Card className="space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm text-zinc-400">監視ソースのトグル</div>
          {saving && <Spinner />}
        </div>
        {settings ? (
          TOGGLES.map((t) => (
            <Switch
              key={t.key}
              label={t.label}
              hint={t.hint}
              checked={Boolean(settings[t.key])}
              disabled={saving}
              onChange={(v) => toggle(t.key, v)}
            />
          ))
        ) : (
          <div className="text-sm text-zinc-500">読み込み中…</div>
        )}
      </Card>

      <p className="text-xs text-zinc-600">
        ここでオンにしたソースだけが「Inbox の監視を1回実行」や常駐デーモンで巡回されます。
        投稿は監視で自動化されず、Inbox で承認したときだけ送信されます。
      </p>
    </div>
  );
}
