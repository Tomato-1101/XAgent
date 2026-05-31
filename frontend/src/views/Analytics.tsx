import { useEffect, useState } from "react";
import { api } from "../api";
import { Card } from "../components/ui";
import type { CostResponse, SummaryResponse } from "../types";

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
        <p className="mt-1 text-sm text-zinc-500">X API 従量課金のコストと下書きの状況。</p>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <div className="text-sm text-zinc-400">累計 API コスト</div>
          <div className="mt-1 text-3xl font-semibold">${cost?.total_usd ?? 0}</div>
          <div className="mt-3 space-y-1 text-xs text-zinc-500">
            {cost &&
              Object.entries(cost.by_kind).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span>
                    {v.units} 件 / ${v.cost_usd.toFixed(3)}
                  </span>
                </div>
              ))}
          </div>
        </Card>

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
    </div>
  );
}
