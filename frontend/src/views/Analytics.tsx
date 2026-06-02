import { useEffect, useState } from "react";
import { api } from "../api";
import { Card } from "../components/ui";
import type { CostGroup, CostResponse, SummaryResponse } from "../types";

/** コスト種別の表示名。 */
const KIND_LABEL: Record<string, string> = {
  read: "読み取り",
  write: "投稿",
  tl: "タイムライン取得",
  llm: "整形・解析(トークン)",
};

function CostBreakdown({ group }: { group: CostGroup }) {
  const entries = Object.entries(group.by_kind);
  return (
    <div className="mt-3 space-y-1 text-xs text-zinc-500">
      {entries.length === 0 && <div>記録なし</div>}
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between">
          <span>{KIND_LABEL[k] ?? k}</span>
          <span>
            {v.units} {k === "llm" ? "トークン" : "件"} / ${v.cost_usd.toFixed(3)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Analytics() {
  const [cost, setCost] = useState<CostResponse | null>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.cost().then(setCost).catch((e) => setError(String(e)));
    api.summary().then(setSummary).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Analytics</h1>
        <p className="mt-1 text-sm text-zinc-500">
          API 従量課金のコスト（X API と Claude API を分けて表示）と下書きの状況。
        </p>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      {/* 合計 */}
      <Card>
        <div className="text-sm text-zinc-400">累計 API コスト（合計）</div>
        <div className="mt-1 text-3xl font-semibold">${cost?.total_usd ?? 0}</div>
        <div className="mt-1 text-xs text-zinc-500">X API + Claude API の合算</div>
      </Card>

      {/* X / Claude 内訳 */}
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <div className="text-sm text-zinc-400">X API</div>
          <div className="mt-1 text-2xl font-semibold">${cost?.x_api.cost_usd ?? 0}</div>
          {cost && <CostBreakdown group={cost.x_api} />}
        </Card>

        <Card>
          <div className="text-sm text-zinc-400">Claude API（クラウド）</div>
          <div className="mt-1 text-2xl font-semibold">${cost?.claude_api.cost_usd ?? 0}</div>
          {cost && <CostBreakdown group={cost.claude_api} />}
        </Card>
      </div>

      <Card>
        <div className="text-sm text-zinc-400">下書きの状況</div>
        <div className="mt-3 space-y-1 text-sm">
          {summary &&
            Object.entries(summary.draft_counts).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-zinc-400">{k}</span>
                <span className="font-medium">{v}</span>
              </div>
            ))}
        </div>
      </Card>
    </div>
  );
}
