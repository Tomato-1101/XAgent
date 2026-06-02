import { useEffect, useState } from "react";
import { api } from "./api";
import { Badge } from "./components/ui";
import { ToastProvider } from "./components/Toast";
import type { Me } from "./types";
import Compose from "./views/Compose";
import Queue from "./views/Queue";
import Posts from "./views/Posts";
import Inbox from "./views/Inbox";
import Targets from "./views/Targets";
import Lists from "./views/Lists";
import Templates from "./views/Templates";
import Style from "./views/Style";
import Settings from "./views/Settings";
import Analytics from "./views/Analytics";
import Agent from "./views/Agent";

type View =
  | "compose"
  | "queue"
  | "posts"
  | "inbox"
  | "targets"
  | "lists"
  | "templates"
  | "style"
  | "settings"
  | "analytics"
  | "agent";

const NAV: { key: View; label: string; desc: string }[] = [
  { key: "compose", label: "Compose", desc: "整形して下書き" },
  { key: "queue", label: "Queue", desc: "下書き・予約・投稿" },
  { key: "posts", label: "Posts", desc: "直近投稿・リポスト" },
  { key: "inbox", label: "Inbox", desc: "返信/絡み案" },
  { key: "targets", label: "Targets", desc: "絡む対象" },
  { key: "lists", label: "Lists", desc: "Xリスト管理" },
  { key: "templates", label: "Templates", desc: "投稿/リプの型" },
  { key: "style", label: "Style", desc: "口調・学習" },
  { key: "settings", label: "Settings", desc: "監視・制限帯" },
  { key: "analytics", label: "Analytics", desc: "コスト/集計" },
  { key: "agent", label: "Agent", desc: "Claude Codeに任せる" },
];

export default function App() {
  const [view, setView] = useState<View>("compose");
  const [online, setOnline] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const ping = () =>
      api
        .health()
        .then(() => setOnline(true))
        .catch(() => setOnline(false));
    ping();
    const t = setInterval(ping, 10000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, [online]);

  return (
    <ToastProvider>
      <div className="flex h-full">
        <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/40 p-4">
          <div className="mb-6">
            <div className="text-lg font-semibold tracking-tight">XAgent</div>
            <div className="mt-1 text-xs text-zinc-500">半自動X運用ダッシュボード</div>
          </div>
          <nav className="flex flex-col gap-1">
            {NAV.map((n) => (
              <button
                key={n.key}
                onClick={() => setView(n.key)}
                className={
                  "rounded-md px-3 py-2 text-left text-sm transition-colors " +
                  (view === n.key ? "bg-sky-600/20 text-sky-300" : "text-zinc-300 hover:bg-zinc-800")
                }
              >
                <div className="font-medium">{n.label}</div>
                <div className="text-xs text-zinc-500">{n.desc}</div>
              </button>
            ))}
          </nav>
          <div className="mt-auto space-y-2 pt-4 text-xs">
            {me?.username && (
              <div className="text-zinc-400">
                投稿先: <span className="text-zinc-200">@{me.username}</span>
              </div>
            )}
            {online === null ? (
              <Badge tone="zinc">接続確認中…</Badge>
            ) : online ? (
              <Badge tone="green">API接続OK</Badge>
            ) : (
              <Badge tone="red">API未接続 (:8000)</Badge>
            )}
          </div>
        </aside>

        <main className="flex-1 overflow-y-auto p-8">
          <div className="mx-auto max-w-3xl">
            {view === "compose" && <Compose />}
            {view === "queue" && <Queue me={me} />}
            {view === "posts" && <Posts />}
            {view === "inbox" && <Inbox me={me} />}
            {view === "targets" && <Targets />}
            {view === "lists" && <Lists />}
            {view === "templates" && <Templates />}
            {view === "style" && <Style />}
            {view === "settings" && <Settings />}
            {view === "analytics" && <Analytics />}
            {view === "agent" && <Agent />}
          </div>
        </main>
      </div>
    </ToastProvider>
  );
}
