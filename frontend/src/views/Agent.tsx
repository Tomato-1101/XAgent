import { AgentHint } from "../components/AgentHint";
import type { AgentPhrase } from "../components/AgentHint";

const COMPOSE: AgentPhrase[] = [
  {
    say: "Claude活用術5選をバズる形で投稿準備して",
    does: "調査 → バズる型を選定 → 本文を書いて下書き作成(承認待ち)",
    cmd: 'xagent compose --raw "<書き上げた本文>"',
  },
  {
    say: "この内容を3案で整形して",
    does: "言い回し違いの下書きをN案生成",
    cmd: 'xagent compose "<メモ>" --variations 3',
  },
  {
    say: "@foo の口調で書いて",
    does: "学習済みアカウントの口調に寄せて整形",
    cmd: 'xagent compose "<メモ>" --emulate foo',
  },
];

const ENGAGE: AgentPhrase[] = [
  {
    say: "このURLにこう絡んで(リプライ)",
    does: "指定ツイートに自分の文でリプライする下書き(元ポスト本文付き)",
    cmd: 'xagent reply <URL> "<返信文>"',
  },
  {
    say: "このURLを引用してこのコメントで",
    does: "引用RTの下書きを作成(引用元本文付き)",
    cmd: 'xagent quote <URL> "<コメント>"',
  },
  {
    say: "絡み案を5件だけ生成して",
    does: "1サイクルの生成数上限を5にして監視を1回実行",
    cmd: "xagent monitor-config --max 5 && xagent monitor-once",
  },
];

const OPS: AgentPhrase[] = [
  {
    say: "承認待ちを見せて",
    does: "下書きを機械可読JSONで一覧(エージェントが内容を把握)",
    cmd: "xagent list --status draft --json",
  },
  {
    say: "#12を承認して予約して",
    does: "承認 → 最適時間に予約(送信は最終承認まで保留)",
    cmd: "xagent approve 12 && xagent queue 12",
  },
];

export default function Agent() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Agent</h1>
        <p className="mt-1 text-sm text-zinc-500">
          このツールの操作は、ターミナルの Claude Code に話しかけると自動で進みます。
          Claude Code が調査・整形・下書き作成・予約まで行い、最終の承認・送信はあなたがここ(WebUI)で行います。
        </p>
      </div>

      <AgentHint title="投稿を作る" phrases={COMPOSE} defaultOpen />
      <AgentHint title="絡む(リプライ / 引用 / 絡み案)" phrases={ENGAGE} defaultOpen />
      <AgentHint title="承認・予約・運用" phrases={OPS} defaultOpen />

      <p className="text-xs text-zinc-600">
        Claude Code は投稿そのものを自動実行しません(X規約・凍結回避)。承認したものだけが送信されます。
        生成数の上限は Settings、または「絡み案をN件だけ」と指示して調整できます。
      </p>
    </div>
  );
}
