import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, Input, Spinner, Switch } from "./ui";
import { ConfirmDialog } from "./ConfirmDialog";
import { useToast } from "./Toast";
import type { TwitterApiKey } from "../types";

/** naive UTC ISO を日本時間の短縮表記に。null は null のまま。 */
function fmtAbs(naiveUtcIso: string | null): string | null {
  if (!naiveUtcIso) return null;
  return new Date(naiveUtcIso + "Z").toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * twitterapi.io 読み取りキーの管理。複数キーを優先度順(上から)に試し、残高切れ/失敗時は
 * 次のキーへ自動フォールバックする。ここで追加/編集/並べ替え/有効無効/削除/疎通テストができる。
 * 平文キーはサーバから返らずマスク表示。新規/差し替え時のみ平文を送信する。
 */
export function TwitterApiKeys() {
  const [keys, setKeys] = useState<TwitterApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null); // 行単位の処理中
  const [adding, setAdding] = useState(false);

  // 追加フォーム
  const [newLabel, setNewLabel] = useState("");
  const [newKey, setNewKey] = useState("");

  // インライン編集
  const [editId, setEditId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editKey, setEditKey] = useState("");

  // 削除確認
  const [delTarget, setDelTarget] = useState<TwitterApiKey | null>(null);
  const [deleting, setDeleting] = useState(false);

  const toast = useToast();

  function load() {
    api.twitterApiKeys().then(setKeys).catch((e) => setError(String(e)));
  }
  useEffect(load, []);

  async function add() {
    if (!newKey.trim()) {
      toast({ tone: "error", message: "APIキーを入力してください。" });
      return;
    }
    setAdding(true);
    setError(null);
    try {
      await api.createTwitterApiKey({ api_key: newKey.trim(), label: newLabel.trim() });
      setNewLabel("");
      setNewKey("");
      load();
      toast({ tone: "success", message: "キーを追加しました。" });
    } catch (e) {
      toast({ tone: "error", message: `追加に失敗: ${String(e)}` });
    } finally {
      setAdding(false);
    }
  }

  async function setEnabled(k: TwitterApiKey, enabled: boolean) {
    setBusyId(k.id);
    try {
      const updated = await api.updateTwitterApiKey(k.id, { enabled });
      setKeys((prev) => prev?.map((x) => (x.id === k.id ? updated : x)) ?? null);
    } catch (e) {
      toast({ tone: "error", message: `更新に失敗: ${String(e)}` });
    } finally {
      setBusyId(null);
    }
  }

  // ↑↓: list の並びを入れ替えて id 順を送り、priority を 0,1,2... に振り直す。
  async function move(index: number, dir: -1 | 1) {
    if (!keys) return;
    const j = index + dir;
    if (j < 0 || j >= keys.length) return;
    const ids = keys.map((k) => k.id);
    [ids[index], ids[j]] = [ids[j], ids[index]];
    setBusyId(keys[index].id);
    try {
      setKeys(await api.reorderTwitterApiKeys(ids));
    } catch (e) {
      toast({ tone: "error", message: `並べ替えに失敗: ${String(e)}` });
    } finally {
      setBusyId(null);
    }
  }

  async function test(k: TwitterApiKey) {
    setBusyId(k.id);
    try {
      const updated = await api.testTwitterApiKey(k.id);
      setKeys((prev) => prev?.map((x) => (x.id === k.id ? updated : x)) ?? null);
      if (updated.last_error) {
        toast({ tone: "error", message: `疎通NG: ${updated.last_error}` });
      } else {
        toast({ tone: "success", message: `疎通OK（${k.label || "キー"}）。` });
      }
    } catch (e) {
      toast({ tone: "error", message: `テストに失敗: ${String(e)}` });
    } finally {
      setBusyId(null);
    }
  }

  function startEdit(k: TwitterApiKey) {
    setEditId(k.id);
    setEditLabel(k.label);
    setEditKey(""); // 空＝据え置き。差し替えるときだけ入力する
  }

  async function saveEdit(k: TwitterApiKey) {
    setBusyId(k.id);
    try {
      const patch: { label?: string; api_key?: string } = { label: editLabel.trim() };
      if (editKey.trim()) patch.api_key = editKey.trim();
      const updated = await api.updateTwitterApiKey(k.id, patch);
      setKeys((prev) => prev?.map((x) => (x.id === k.id ? updated : x)) ?? null);
      setEditId(null);
      toast({ tone: "success", message: "保存しました。" });
    } catch (e) {
      toast({ tone: "error", message: `保存に失敗: ${String(e)}` });
    } finally {
      setBusyId(null);
    }
  }

  async function doDelete() {
    if (!delTarget) return;
    setDeleting(true);
    try {
      await api.deleteTwitterApiKey(delTarget.id);
      setDelTarget(null);
      load();
      toast({ tone: "success", message: "キーを削除しました。" });
    } catch (e) {
      toast({ tone: "error", message: `削除に失敗: ${String(e)}` });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Card className="space-y-4">
      <div>
        <div className="text-sm text-zinc-200">twitterapi.io 読み取りキー（複数・フォールバック）</div>
        <div className="mt-1 text-xs text-zinc-500">
          他人の投稿の読み取りに使うキー。上から順に試し、残高切れ（402）や失敗時は次のキーへ
          自動でフォールバックします。全キー失敗で初めて公式APIに切り替わります。
          並び順＝優先度（↑が先）。キーは末尾4文字だけ表示します。
        </div>
      </div>

      {error && <div className="text-sm text-red-300">エラー: {error}</div>}

      {keys === null ? (
        <div className="text-sm text-zinc-500">読み込み中…</div>
      ) : keys.length === 0 ? (
        <div className="text-sm text-zinc-500">
          キーが未登録です。下のフォームから追加してください（未登録のままだと読み取りは公式APIのみになります）。
        </div>
      ) : (
        <div className="space-y-2">
          {keys.map((k, i) => {
            const editing = editId === k.id;
            const rowBusy = busyId === k.id;
            return (
              <div
                key={k.id}
                className={
                  "rounded-md border p-3 " +
                  (k.enabled ? "border-zinc-700 bg-zinc-900/40" : "border-zinc-800 bg-zinc-950 opacity-70")
                }
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-zinc-500">#{i + 1}</span>
                  <div className="flex flex-col">
                    <button
                      className="text-zinc-500 hover:text-zinc-200 disabled:opacity-30"
                      title="優先度を上げる"
                      disabled={i === 0 || rowBusy}
                      onClick={() => move(i, -1)}
                    >
                      ▲
                    </button>
                    <button
                      className="text-zinc-500 hover:text-zinc-200 disabled:opacity-30"
                      title="優先度を下げる"
                      disabled={i === keys.length - 1 || rowBusy}
                      onClick={() => move(i, 1)}
                    >
                      ▼
                    </button>
                  </div>

                  {editing ? (
                    <Input
                      value={editLabel}
                      placeholder="名前（例: メイン）"
                      className="w-40"
                      onChange={(e) => setEditLabel(e.target.value)}
                    />
                  ) : (
                    <span className="text-sm text-zinc-200">{k.label || "(名前なし)"}</span>
                  )}
                  <span className="font-mono text-xs text-zinc-500">{k.key_masked}</span>

                  <div className="ml-auto flex items-center gap-2">
                    {rowBusy && <Spinner />}
                    <div className="w-20">
                      <Switch
                        label="有効"
                        checked={k.enabled}
                        disabled={rowBusy}
                        onChange={(v) => setEnabled(k, v)}
                      />
                    </div>
                  </div>
                </div>

                {/* 疎通ステータス */}
                <div className="mt-1 text-xs">
                  {k.last_error ? (
                    <span className="text-red-400" title={k.last_error}>
                      疎通NG: {k.last_error.length > 60 ? k.last_error.slice(0, 60) + "…" : k.last_error}
                    </span>
                  ) : k.last_ok_at ? (
                    <span className="text-green-400/80">疎通OK（{fmtAbs(k.last_ok_at)}）</span>
                  ) : (
                    <span className="text-zinc-600">未テスト</span>
                  )}
                </div>

                {editing && (
                  <div className="mt-2">
                    <Input
                      type="password"
                      value={editKey}
                      placeholder="キーを差し替える場合のみ入力（空欄なら据え置き）"
                      onChange={(e) => setEditKey(e.target.value)}
                    />
                  </div>
                )}

                <div className="mt-2 flex flex-wrap gap-2">
                  {editing ? (
                    <>
                      <Button size="sm" onClick={() => saveEdit(k)} disabled={rowBusy}>
                        {rowBusy ? <Spinner /> : "保存"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditId(null)} disabled={rowBusy}>
                        取消
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button size="sm" variant="outline" onClick={() => test(k)} disabled={rowBusy}>
                        テスト
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => startEdit(k)} disabled={rowBusy}>
                        編集
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setDelTarget(k)} disabled={rowBusy}>
                        削除
                      </Button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 追加フォーム */}
      <div className="space-y-2 border-t border-zinc-800 pt-3">
        <div className="text-xs text-zinc-500">キーを追加</div>
        <div className="flex flex-wrap gap-2">
          <Input
            value={newLabel}
            placeholder="名前（例: 予備1）"
            className="w-40"
            onChange={(e) => setNewLabel(e.target.value)}
          />
          <Input
            type="password"
            value={newKey}
            placeholder="twitterapi.io の APIキー"
            className="min-w-[16rem] flex-1"
            onChange={(e) => setNewKey(e.target.value)}
          />
          <Button onClick={add} disabled={adding}>
            {adding ? <Spinner /> : "追加"}
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={delTarget !== null}
        title="このキーを削除しますか？"
        description={
          delTarget
            ? `「${delTarget.label || "(名前なし)"}」（${delTarget.key_masked}）を削除します。読み取りで使えなくなります。`
            : ""
        }
        confirmLabel="削除"
        confirmVariant="danger"
        busy={deleting}
        onConfirm={doDelete}
        onCancel={() => setDelTarget(null)}
      />
    </Card>
  );
}
