import { useEffect, useState } from "react";
import { api } from "./api";
import { Badge } from "./components/ui";
import { ToastProvider } from "./components/Toast";
import type { Me, SchedulerStatus } from "./types";
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
import Login from "./views/Login";

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

/** 予約スケジューラ(予約投稿の常駐)の稼働状況をバッジ用に色とラベルへ変換する。 */
function schedBadge(s: SchedulerStatus): { tone: "green" | "red" | "amber"; label: string } {
  if (!s.scheduler_enabled) return { tone: "amber", label: "予約自動発火 無効(設定)" };
  if (s.healthy) return { tone: "green", label: "予約スケジューラ 稼働中" };
  return { tone: "red", label: "予約スケジューラ 停止?" };
}

export default function App() {
  const [view, setView] = useState<View>("compose");
  const [online, setOnline] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [sched, setSched] = useState<SchedulerStatus | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  // lg未満のドロワー開閉(lg以上では常時表示なので無関係)
  const [navOpen, setNavOpen] = useState(false);

  // サーバが API_TOKEN を設定しているときだけ 401→このイベントが飛ぶ(ローカル運用では出ない)。
  useEffect(() => {
    const onUnauthorized = () => setNeedLogin(true);
    window.addEventListener("xagent:unauthorized", onUnauthorized);
    return () => window.removeEventListener("xagent:unauthorized", onUnauthorized);
  }, []);

  useEffect(() => {
    const ping = () => {
      api
        .health()
        .then(() => setOnline(true))
        .catch(() => setOnline(false));
      api
        .status()
        .then(setSched)
        .catch(() => setSched(null));
    };
    ping();
    const t = setInterval(ping, 10000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, [online]);

  if (needLogin) {
    return (
      <Login
        onSuccess={() => {
          setNeedLogin(false);
          api.health().then(() => setOnline(true)).catch(() => setOnline(false));
          api.status().then(setSched).catch(() => setSched(null));
          api.me().then(setMe).catch(() => setMe(null));
        }}
      />
    );
  }

  return (
    <ToastProvider>
      <div className="flex h-full flex-col lg:flex-row">
        {/* モバイル用ヘッダ(lg以上では非表示)。ハンバーガーでドロワーを開く */}
        <header className="sticky top-0 z-40 flex shrink-0 items-center gap-3 border-b border-zinc-800 bg-zinc-950/95 px-4 py-3 lg:hidden">
          <button
            onClick={() => setNavOpen(true)}
            aria-label="メニューを開く"
            className="flex h-9 w-9 flex-col items-center justify-center gap-1 rounded-md hover:bg-zinc-800"
          >
            <span className="h-0.5 w-5 rounded bg-zinc-300" />
            <span className="h-0.5 w-5 rounded bg-zinc-300" />
            <span className="h-0.5 w-5 rounded bg-zinc-300" />
          </button>
          <div className="text-base font-semibold tracking-tight">XAgent</div>
          <div className="ml-auto">
            {online === null ? (
              <Badge tone="zinc">確認中</Badge>
            ) : online ? (
              <Badge tone="green">接続OK</Badge>
            ) : (
              <Badge tone="red">未接続</Badge>
            )}
          </div>
        </header>

        {navOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/60 lg:hidden"
            onClick={() => setNavOpen(false)}
          />
        )}

        <aside
          className={
            "fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col overflow-y-auto border-r border-zinc-800 " +
            "bg-zinc-900 p-4 transition-transform lg:static lg:w-56 lg:translate-x-0 lg:bg-zinc-900/40 " +
            (navOpen ? "translate-x-0" : "-translate-x-full")
          }
        >
          <div className="mb-6">
            <div className="text-lg font-semibold tracking-tight">XAgent</div>
            <div className="mt-1 text-xs text-zinc-500">半自動X運用ダッシュボード</div>
          </div>
          <nav className="flex flex-col gap-1">
            {NAV.map((n) => (
              <button
                key={n.key}
                onClick={() => {
                  setView(n.key);
                  setNavOpen(false);
                }}
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
            {online && sched && (
              <div
                title={
                  sched.last_queue_tick_at
                    ? `直近の発火 ${Math.round(sched.seconds_since_queue_tick ?? 0)}秒前` +
                      `（${sched.queue_interval_seconds}秒ごと）`
                    : "まだ一度も発火していません"
                }
              >
                <Badge tone={schedBadge(sched).tone}>{schedBadge(sched).label}</Badge>
              </div>
            )}
            {online && sched && !sched.posting_enabled && (
              <Badge tone="amber">投稿 緊急停止中</Badge>
            )}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
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
