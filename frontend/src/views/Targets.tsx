import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input } from "../components/ui";
import { useToast } from "../components/Toast";
import type { Target, XList } from "../types";

export default function Targets() {
  const [targets, setTargets] = useState<Target[]>([]);
  const [lists, setLists] = useState<XList[]>([]);
  const [handle, setHandle] = useState("");
  const [listId, setListId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  function reload() {
    api.listTargets().then(setTargets).catch((e) => setError(String(e)));
  }
  useEffect(reload, []);
  useEffect(() => {
    api.lists().then(setLists).catch(() => setLists([]));
  }, []);

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

  async function addList() {
    const l = lists.find((x) => x.id === listId);
    if (!l) return;
    setError(null);
    try {
      await api.addTargetList(l.name, l.id);
      setListId("");
      toast({ tone: "success", message: `リスト「${l.name}」を対象に追加しました。` });
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

  // 既に対象登録済みのリストはプルダウンから除く
  const addedListIds = new Set(targets.filter((t) => t.kind === "list").map((t) => t.list_id));
  const availableLists = lists.filter((l) => !addedListIds.has(l.id));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Targets</h1>
        <p className="mt-1 text-sm text-zinc-500">
          絡む対象。新規投稿を監視して引用RT案を生成します。リストを対象にすると、リストの
          メンバー全員が監視され、リストを更新すると自動で連携します（個別削除は不要）。
        </p>
      </div>

      {/* リストを丸ごと対象に追加(推奨) */}
      <Card className="space-y-2">
        <div className="text-sm text-zinc-300">リストを対象に追加</div>
        <div className="flex gap-2">
          <select
            value={listId}
            onChange={(e) => setListId(e.target.value)}
            className="flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
          >
            <option value="">リストを選択…</option>
            {availableLists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}（{l.member_count}人）
              </option>
            ))}
          </select>
          <Button onClick={addList} disabled={!listId}>
            追加
          </Button>
        </div>
        <p className="text-xs text-zinc-500">
          リスト対象を削除すると、そのリスト全体が監視から一括で外れます。
        </p>
      </Card>

      {/* 個別ハンドルを追加 */}
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
          <Card key={t.id} className="flex items-center justify-between gap-2 py-3">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              {t.kind === "list" ? (
                <>
                  <span className="font-medium">📋 {t.handle}</span>
                  <Badge tone="violet">リスト連携</Badge>
                  <span className="text-xs text-zinc-500">メンバー全員を監視</span>
                </>
              ) : (
                <>
                  <span className="font-medium">@{t.handle}</span>
                  {t.user_id ? (
                    <Badge tone="green">解決済み</Badge>
                  ) : (
                    <Badge tone="amber">user_id未解決</Badge>
                  )}
                  <span className="text-xs text-zinc-500">{t.kind}</span>
                </>
              )}
            </div>
            <Button size="sm" variant="danger" className="shrink-0" onClick={() => remove(t.id)}>
              削除
            </Button>
          </Card>
        ))}
      </div>
    </div>
  );
}
