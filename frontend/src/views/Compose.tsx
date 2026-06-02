import { useEffect, useRef, useState } from "react";
import { api, mediaUrl } from "../api";
import { Badge, Button, Card, Spinner, Textarea } from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DraftCard } from "../components/DraftCard";
import { AgentHint } from "../components/AgentHint";
import type { AgentPhrase } from "../components/AgentHint";
import type { AccountProfile, Draft, Interpreted, MediaItem, PreviewResponse } from "../types";

const MAX_IMAGES = 4;

const AGENT_PHRASES: AgentPhrase[] = [
  {
    say: "Claude活用術5選をバズる形で投稿準備して",
    does: "調査 → バズ型を選定 → 本文を書いて下書き作成(承認待ち)",
    cmd: 'xagent compose --raw "<書き上げた本文>"',
  },
  {
    say: "この内容を3案で整形して",
    does: "言い回し違いの下書きをN案生成",
    cmd: 'xagent compose "<メモ>" --variations 3',
  },
];

// 指令(ツイートURL / そのまま投稿の指示)を含むかの安価な事前判定。
const TWEET_URL_RE = /(?:x|twitter)\.com\/[A-Za-z0-9_]+\/status\/\d+/i;
const RAW_HINT_RE = /(そのまま|加工しないで|加工せず|整形しないで|整形せず|原文|このまま投稿)/;
function hasCommandMarkers(t: string): boolean {
  return TWEET_URL_RE.test(t) || RAW_HINT_RE.test(t);
}

export default function Compose() {
  const [text, setText] = useState("");
  const [allowLong, setAllowLong] = useState(false);
  const [raw, setRaw] = useState(false);
  const [emulate, setEmulate] = useState("");
  const [nVariations, setNVariations] = useState(1);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [interpreting, setInterpreting] = useState(false);
  const [interp, setInterp] = useState<Interpreted | null>(null);
  const [cmdAction, setCmdAction] = useState<"quote" | "post">("post");
  const [cmdRaw, setCmdRaw] = useState(false);
  const [cmdBody, setCmdBody] = useState("");
  const [created, setCreated] = useState<Draft[]>([]);
  const [profiles, setProfiles] = useState<AccountProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listProfiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);

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

  const images = media.filter((m) => m.kind === "image");
  const hasVideo = media.some((m) => m.kind === "video");
  const isCommand = hasCommandMarkers(text);

  async function onFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    setUploading(true);
    try {
      // 現在の構成にローカルで重ねて検証(画像最大4枚 / 動画1個 / 混在不可)
      let imgCount = images.length;
      let vidCount = hasVideo ? 1 : 0;
      const added: MediaItem[] = [];
      for (const file of Array.from(files)) {
        const isVideo = file.type.startsWith("video/");
        if (isVideo) {
          if (vidCount >= 1 || imgCount > 0 || added.some((a) => a.kind === "image")) {
            throw new Error("動画は1個まで・画像とは併用できません。");
          }
          vidCount += 1;
        } else {
          if (vidCount >= 1) throw new Error("動画と画像は併用できません。");
          if (imgCount >= MAX_IMAGES) throw new Error(`画像は最大${MAX_IMAGES}枚までです。`);
          imgCount += 1;
        }
        const item = await api.uploadMedia(file);
        added.push(item);
      }
      setMedia((prev) => [...prev, ...added]);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function removeMedia(path: string) {
    setMedia((prev) => prev.filter((m) => m.path !== path));
  }

  function resetInput() {
    setText("");
    setMedia([]);
    setRaw(false);
  }

  // 主ボタン: 指令(URL/そのまま指示)を含むなら解析して確認、なければ通常整形。
  async function onSubmit() {
    if (!text.trim()) return;
    if (isCommand) {
      setError(null);
      setInterpreting(true);
      try {
        const r = await api.interpret(text);
        setInterp(r);
        setCmdAction(r.target_tweet_id ? r.action : "post");
        setCmdRaw(r.raw || raw);
        setCmdBody(r.body);
      } catch (e) {
        setError(String(e));
      } finally {
        setInterpreting(false);
      }
      return;
    }
    await compose();
  }

  async function compose() {
    setBusy(true);
    setError(null);
    setCreated([]);
    try {
      const handle = emulate || undefined;
      const paths = media.map((m) => m.path);
      if (!raw && nVariations > 1) {
        const ds = await api.composeVariations(text, nVariations, allowLong, handle, paths);
        setCreated(ds);
      } else {
        const d = await api.compose(text, allowLong, handle, paths, undefined, raw);
        setCreated([d]);
      }
      resetInput();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmCommand() {
    if (!interp) return;
    setBusy(true);
    setError(null);
    try {
      const handle = emulate || undefined;
      const paths = media.map((m) => m.path);
      const d = await api.command({
        action: cmdAction,
        text: cmdBody,
        target_tweet_id: cmdAction === "quote" ? interp.target_tweet_id : null,
        target_handle: interp.target_handle,
        raw: cmdRaw,
        allow_long: allowLong,
        emulate_handle: handle,
        media_paths: paths,
      });
      setCreated([d]);
      setInterp(null);
      resetInput();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const submitLabel = isCommand
    ? "指令を解析"
    : raw
      ? "そのまま下書き作成"
      : nVariations > 1
        ? `${nVariations}案を作成`
        : "整形して下書き作成";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Compose</h1>
        <p className="mt-1 text-sm text-zinc-500">
          思いついた内容を投げると、あなたの口調に整形して下書きにします（承認するまで投稿しません）。
        </p>
      </div>

      <AgentHint title="調査して本文まで書かせる" phrases={AGENT_PHRASES} />


      <Card className="space-y-3">
        <Textarea
          rows={5}
          placeholder="ここに思いついたことを書く（音声入力で入れてもOK）…&#10;ツイートURL＋「リポストして以下の文で投稿」のように書くと、引用リポストとして解釈します。"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />

        {isCommand && (
          <div className="text-xs text-sky-300">
            指令（URL/「そのまま」等）を検出。ボタンで解析し、内容を確認してから下書きにします。
          </div>
        )}

        {/* 画像/動画の添付 */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime"
              multiple
              className="hidden"
              onChange={(e) => onFiles(e.target.files)}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileRef.current?.click()}
              disabled={uploading || hasVideo || images.length >= MAX_IMAGES}
            >
              {uploading ? <Spinner /> : "画像/動画を追加"}
            </Button>
            <span className="text-xs text-zinc-500">
              画像は最大{MAX_IMAGES}枚、または動画1個（混在不可）
            </span>
          </div>

          {media.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {media.map((m) => (
                <div
                  key={m.path}
                  className="relative h-20 w-20 overflow-hidden rounded-md border border-zinc-700 bg-zinc-950"
                >
                  {m.kind === "image" ? (
                    <img src={mediaUrl(m.path)} alt={m.filename} className="h-full w-full object-cover" />
                  ) : (
                    <video src={mediaUrl(m.path)} className="h-full w-full object-cover" muted />
                  )}
                  <button
                    onClick={() => removeMedia(m.path)}
                    className="absolute right-0 top-0 bg-black/70 px-1 text-xs text-white hover:bg-black"
                    aria-label="削除"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm text-zinc-400">
            <span>真似る相手（任意・選択時のみ反映）</span>
            <select
              value={emulate}
              onChange={(e) => setEmulate(e.target.value)}
              disabled={raw}
              className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50 disabled:opacity-50"
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
              disabled={raw}
              className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50 disabled:opacity-50"
            >
              <option value={1}>1案（単発）</option>
              <option value={2}>2案</option>
              <option value={3}>3案</option>
              <option value={4}>4案</option>
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-2 border-t border-zinc-800 pt-3">
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={allowLong}
              onChange={(e) => setAllowLong(e.target.checked)}
            />
            長文を許可（「さらに表示」で釣る用。既定は折りたたみ回避）
          </label>
          <label className="flex items-center gap-2 text-sm text-zinc-400">
            <input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} />
            整形せずそのまま投稿（自分で書いた文をAI加工しない）
          </label>
        </div>

        <div className="flex items-center justify-end">
          <Button onClick={onSubmit} disabled={busy || interpreting || uploading || !text.trim()}>
            {busy || interpreting ? <Spinner /> : submitLabel}
          </Button>
        </div>

        {preview && (
          <div className="flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-3 text-xs">
            <span className="text-zinc-500">プレビュー:</span>
            <Badge tone={preview.char_length > 140 ? "red" : "zinc"}>
              {preview.char_length}/140字
            </Badge>
            {allowLong ? (
              <Badge tone="amber">長文許可（さらに表示で折りたたみ可）</Badge>
            ) : preview.folded ? (
              <Badge tone="amber">140字超のため複数に分割</Badge>
            ) : (
              <Badge tone="green">140字以内（全文表示）</Badge>
            )}
            <Badge tone="sky">{preview.segments.length} 件</Badge>
            {raw && <Badge tone="amber">そのまま投稿（整形なし）</Badge>}
            {media.length > 0 && <Badge tone="violet">メディア {media.length}</Badge>}
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

      {/* 指令の確認(引用RT/通常投稿・整形/そのまま・本文を確認してから作成) */}
      <ConfirmDialog
        open={interp !== null}
        title="指令を検出しました"
        description={interp?.note || undefined}
        confirmLabel="この内容で下書き作成"
        busy={busy}
        onConfirm={confirmCommand}
        onCancel={() => setInterp(null)}
      >
        {interp && (
          <div className="space-y-3">
            <div className="space-y-1">
              <div className="text-xs text-zinc-500">この入力をどう扱うか</div>
              <div className="flex gap-2">
                <button
                  disabled={!interp.target_tweet_id}
                  onClick={() => setCmdAction("quote")}
                  className={
                    "rounded-md border px-3 py-1 text-sm disabled:opacity-40 " +
                    (cmdAction === "quote"
                      ? "border-sky-500 bg-sky-500/15 text-sky-200"
                      : "border-zinc-700 text-zinc-300 hover:bg-zinc-800")
                  }
                >
                  引用リポスト
                </button>
                <button
                  onClick={() => setCmdAction("post")}
                  className={
                    "rounded-md border px-3 py-1 text-sm " +
                    (cmdAction === "post"
                      ? "border-sky-500 bg-sky-500/15 text-sky-200"
                      : "border-zinc-700 text-zinc-300 hover:bg-zinc-800")
                  }
                >
                  通常投稿
                </button>
              </div>
            </div>

            {cmdAction === "quote" && interp.target_url && (
              <div className="text-xs text-zinc-400">
                引用先:{" "}
                <a
                  href={interp.target_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-300 underline hover:text-sky-200"
                >
                  {interp.target_handle ? `@${interp.target_handle}` : interp.target_url}
                </a>
              </div>
            )}

            <label className="block space-y-1">
              <span className="text-xs text-zinc-500">
                {cmdAction === "quote" ? "引用コメント本文" : "投稿本文"}
              </span>
              <Textarea rows={4} value={cmdBody} onChange={(e) => setCmdBody(e.target.value)} />
            </label>

            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={cmdRaw}
                onChange={(e) => setCmdRaw(e.target.checked)}
              />
              整形せずそのまま投稿する
            </label>
          </div>
        )}
      </ConfirmDialog>
    </div>
  );
}
