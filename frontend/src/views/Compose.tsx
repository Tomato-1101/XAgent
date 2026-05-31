import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Spinner, Textarea } from "../components/ui";
import { DraftCard } from "../components/DraftCard";
import type { AccountProfile, Draft, PreviewResponse } from "../types";

export default function Compose() {
  const [text, setText] = useState("");
  const [allowLong, setAllowLong] = useState(false);
  const [emulate, setEmulate] = useState("");
  const [nVariations, setNVariations] = useState(1);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Draft[]>([]);
  const [profiles, setProfiles] = useState<AccountProfile[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listProfiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);

  // 入力に応じて構造プレビュー(LLM不使用)をデバウンス取得
  useEffect(() => {
    if (!text.trim()) {
      setPreview(null);
      return;
    }
    const t = setTimeout(() => {
      api.preview(text, allowLong).then(setPreview).catch(() => setPreview(null));
    }, 400);
    return () => clearTimeout(t);
  }, [text, allowLong]);

  async function compose() {
    setBusy(true);
    setError(null);
    setCreated([]);
    try {
      const handle = emulate || undefined;
      if (nVariations > 1) {
        const ds = await api.composeVariations(text, nVariations, allowLong, handle);
        setCreated(ds);
      } else {
        const d = await api.compose(text, allowLong, handle);
        setCreated([d]);
      }
      setText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Compose</h1>
        <p className="mt-1 text-sm text-zinc-500">
          思いついた内容を投げると、あなたの口調に整形して下書きにします（承認するまで投稿しません）。
        </p>
      </div>

      <Card className="space-y-3">
        <Textarea
          rows={5}
          placeholder="ここに思いついたことを書く（音声入力で入れてもOK）…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm text-zinc-400">
            <span>真似る相手（任意・選択時のみ反映）</span>
            <select
              value={emulate}
              onChange={(e) => setEmulate(e.target.value)}
              className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
            >
              <option value="">なし（常時のスタイルガイドのみ）</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.handle}>
                  @{p.handle}
                  {p.is_self ? "（自分）" : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1 text-sm text-zinc-400">
            <span>案の数</span>
            <select
              value={nVariations}
              onChange={(e) => setNVariations(Number(e.target.value))}
              className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
            >
              <option value={1}>1案（単発）</option>
              <option value={2}>2案</option>
              <option value={3}>3案</option>
              <option value={4}>4案</option>
            </select>
          </label>
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={allowLong}
              onChange={(e) => setAllowLong(e.target.checked)}
            />
            長文を許可（「さらに表示」で釣る用。既定は折りたたみ回避）
          </label>
          <Button onClick={compose} disabled={busy || !text.trim()}>
            {busy ? <Spinner /> : nVariations > 1 ? `${nVariations}案を作成` : "整形して下書き作成"}
          </Button>
        </div>

        {preview && (
          <div className="flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-3 text-xs">
            <span className="text-zinc-500">プレビュー:</span>
            <Badge tone="zinc">加重 {preview.weighted_length}</Badge>
            {preview.folded ? (
              <Badge tone="amber">さらに表示で折りたたみ</Badge>
            ) : (
              <Badge tone="green">全文表示される長さ</Badge>
            )}
            <Badge tone="sky">{preview.segments.length} スレッド</Badge>
          </div>
        )}
      </Card>

      {error && (
        <Card className="border-red-900/60 text-sm text-red-300">
          エラー: {error}
          <div className="mt-1 text-xs text-zinc-500">
            整形にはバックエンドの ANTHROPIC_API_KEY 設定が必要です。
          </div>
        </Card>
      )}

      {created.length > 0 && (
        <div className="space-y-2">
          <div className="text-sm text-zinc-400">
            {created.length} 件作成しました。Queueタブで承認・投稿できます。
          </div>
          {created.map((d) => (
            <DraftCard key={d.id} draft={d} />
          ))}
        </div>
      )}
    </div>
  );
}
