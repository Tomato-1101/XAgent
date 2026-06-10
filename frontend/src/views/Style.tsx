import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input, Spinner, Switch, Textarea } from "../components/ui";
import type { AccountProfile } from "../types";

function topHours(json: string): string {
  try {
    const arr = JSON.parse(json) as [number, number][];
    const top = [...arr]
      .filter((x) => Array.isArray(x) && x[1] > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([h]) => `${h}時`);
    return top.length ? top.join(" / ") : "—";
  } catch {
    return "—";
  }
}

export default function Style() {
  const [guide, setGuide] = useState("");
  const [examples, setExamples] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [profiles, setProfiles] = useState<AccountProfile[]>([]);
  const [handle, setHandle] = useState("");
  const [isSelf, setIsSelf] = useState(false);
  const [maxTotal, setMaxTotal] = useState(200);
  const [learning, setLearning] = useState(false);

  function reload() {
    api
      .getStyle()
      .then((s) => {
        setGuide(s.guide_text);
        setExamples(s.examples);
      })
      .catch((e) => setError(String(e)));
    api.listProfiles().then(setProfiles).catch(() => setProfiles([]));
  }
  useEffect(reload, []);

  async function save() {
    setError(null);
    setInfo(null);
    try {
      await api.putStyle(guide);
      setInfo("スタイルガイドを保存しました。");
    } catch (e) {
      setError(String(e));
    }
  }

  async function learnOwn() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await api.learn();
      setInfo(`自分の過去投稿を ${r.saved} 件学習しました。`);
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function learnAccount() {
    const h = handle.trim().replace(/^@/, "");
    if (!h) return;
    setLearning(true);
    setError(null);
    setInfo(null);
    try {
      const p = await api.learnProfile(h, maxTotal, isSelf);
      setInfo(`@${p.handle} を学習しました（${p.posts_fetched}件）。`);
      setHandle("");
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setLearning(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Style</h1>
        <p className="mt-1 text-sm text-zinc-500">
          常時適用される口調は「スタイルガイド」で指定します。学習プロファイルは投稿時に「真似る相手」を選んだ時だけ使われます。
        </p>
      </div>

      <Card className="space-y-3">
        <div className="text-sm text-zinc-400">スタイルガイド（常時適用・手入力）</div>
        <Textarea
          rows={6}
          placeholder="例: 一人称は俺。断定口調。専門用語は噛み砕く。煽らない。絵文字なし。"
          value={guide}
          onChange={(e) => setGuide(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button onClick={save}>保存</Button>
          <Button variant="outline" onClick={learnOwn} disabled={busy}>
            {busy ? <Spinner /> : "自分の過去投稿を学習 (X API)"}
          </Button>
        </div>
        {info && <div className="text-sm text-sky-300">{info}</div>}
        {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      </Card>

      <Card className="space-y-3">
        <div className="text-sm text-zinc-400">アカウント学習（口調・内容・投稿時間をAIが抽出してDB保存）</div>
        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <Input
            placeholder="@handle"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && learnAccount()}
          />
          <select
            value={maxTotal}
            onChange={(e) => setMaxTotal(Number(e.target.value))}
            className="h-9 rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
          >
            <option value={50}>最大50件</option>
            <option value={100}>最大100件</option>
            <option value={200}>最大200件</option>
          </select>
          <Button onClick={learnAccount} disabled={learning || !handle.trim()}>
            {learning ? <Spinner /> : "学習する"}
          </Button>
        </div>
        <div className="w-56">
          <Switch label="自分のアカウント" hint="is_self" checked={isSelf} onChange={setIsSelf} />
        </div>
        <p className="text-xs text-zinc-600">
          取得した投稿はDBに保存し、口調/内容テーマ/投稿の型/フック/ハッシュタグ傾向/投稿時間帯を抽出します。
          自動では整形に注入されません（Composeで「真似る相手」に選んだ時のみ）。
        </p>
      </Card>

      <Card className="space-y-2">
        <div className="text-sm text-zinc-400">学習済みプロファイル</div>
        {profiles.length === 0 ? (
          <div className="text-sm text-zinc-500">まだありません。上で @handle を学習してください。</div>
        ) : (
          <div className="space-y-2">
            {profiles.map((p) => (
              <div key={p.id} className="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">@{p.handle}</span>
                  {p.is_self && <Badge tone="sky">自分</Badge>}
                  <span className="text-xs text-zinc-500">
                    {p.posts_fetched}件 ・ ♥{p.avg_likes.toFixed(1)} ・ RT{p.avg_retweets.toFixed(1)} ・ 投稿時間 {topHours(p.active_hours_json)}
                  </span>
                </div>
                {p.profile_text && (
                  <p className="mt-1 whitespace-pre-wrap text-zinc-300">{p.profile_text}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="space-y-2">
        <div className="text-sm text-zinc-400">自分の学習済み投稿（例・上位）</div>
        {examples.length === 0 ? (
          <div className="text-sm text-zinc-500">まだありません。「自分の過去投稿を学習」を実行してください。</div>
        ) : (
          <ul className="space-y-1 text-sm text-zinc-300">
            {examples.map((e, i) => (
              <li key={i} className="rounded border border-zinc-800 bg-zinc-950 p-2">
                {e}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
