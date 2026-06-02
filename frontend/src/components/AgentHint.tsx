import { useState } from "react";

/** 「ユーザーが言う言葉 → Claude Code がやること → 等価なCLI(参考)」の1項目。 */
export interface AgentPhrase {
  say: string; // ターミナルの Claude Code に話しかける言葉
  does: string; // Claude Code が行う操作
  cmd?: string; // 等価な CLI(参考・コピー可)
}

/**
 * この画面の操作を「Claude Code に話せば自動でできる」ことを示す折りたたみヒント。
 * 人間(WebUIで承認/送信)とエージェント(調査/整形/下書き作成)の役割分担を明確にする。
 */
export function AgentHint({
  title = "Claude Code に任せる",
  phrases,
  defaultOpen = false,
}: {
  title?: string;
  phrases: AgentPhrase[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [copied, setCopied] = useState<number | null>(null);

  async function copy(text: string, i: number) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(i);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      /* clipboard 不可環境は無視 */
    }
  }

  return (
    <div className="rounded-lg border border-sky-900/50 bg-sky-950/20 p-3 text-sm">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 text-left text-sky-300"
      >
        <span>🤖 {title}</span>
        <span className="shrink-0 text-xs text-sky-400/70">
          {open ? "閉じる" : "ターミナルのClaude Codeに話すと自動でできます"}
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          {phrases.map((p, i) => (
            <div key={i} className="rounded-md border border-zinc-800 bg-zinc-950 p-2">
              <div className="text-zinc-200">「{p.say}」</div>
              <div className="mt-0.5 text-xs text-zinc-500">→ {p.does}</div>
              {p.cmd && (
                <div className="mt-1 flex items-center gap-2">
                  <code className="flex-1 overflow-x-auto whitespace-nowrap rounded bg-black/40 px-2 py-1 text-xs text-zinc-400">
                    {p.cmd}
                  </code>
                  <button
                    onClick={() => copy(p.cmd as string, i)}
                    className="shrink-0 rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
                  >
                    {copied === i ? "コピー済" : "コピー"}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
