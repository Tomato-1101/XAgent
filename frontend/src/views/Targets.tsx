import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input } from "../components/ui";
import type { Target } from "../types";

export default function Targets() {
  const [targets, setTargets] = useState<Target[]>([]);
  const [handle, setHandle] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api.listTargets().then(setTargets).catch((e) => setError(String(e)));
  }
  useEffect(reload, []);

  async function add() {
    if (!handle.trim()) return;
    setError(null);
    try {
      await api.addTarget(handle.trim());
      setHandle("");
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  async function remove(id: number) {
    setError(null);
    try {
      await api.deleteTarget(id);
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Targets</h1>
        <p className="mt-1 text-sm text-zinc-500">
          絡む対象（有名人・インフルエンサー）。新規投稿を監視して引用RT案を生成します。
        </p>
      </div>

      <Card className="flex gap-2">
        <Input
          placeholder="@handle を追加"
          value={handle}
          onChange={(e) => setHandle(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button onClick={add}>追加</Button>
      </Card>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}
      {targets.length === 0 && <div className="text-sm text-zinc-500">対象がありません。</div>}

      <div className="space-y-2">
        {targets.map((t) => (
          <Card key={t.id} className="flex items-center justify-between py-3">
            <div className="flex items-center gap-3">
              <span className="font-medium">@{t.handle}</span>
              {t.user_id ? (
                <Badge tone="green">解決済み</Badge>
              ) : (
                <Badge tone="amber">user_id未解決</Badge>
              )}
              <span className="text-xs text-zinc-500">{t.kind}</span>
            </div>
            <Button size="sm" variant="danger" onClick={() => remove(t.id)}>
              削除
            </Button>
          </Card>
        ))}
      </div>
    </div>
  );
}
