import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input, Spinner, Switch, Textarea } from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import type { XList, XListMember } from "../types";

export default function Lists() {
  const [lists, setLists] = useState<XList[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<XList | null>(null);
  const [members, setMembers] = useState<XListMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  // 新規作成フォーム
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPrivate, setNewPrivate] = useState(true);
  const [newAccounts, setNewAccounts] = useState("");

  // 選択中リストの設定編集
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editPrivate, setEditPrivate] = useState(true);

  // メンバー追加
  const [addHandle, setAddHandle] = useState("");

  // 削除確認
  const [toDelete, setToDelete] = useState<XList | null>(null);

  function loadLists() {
    setLoading(true);
    setError(null);
    api
      .lists()
      .then(setLists)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(loadLists, []);

  function loadMembers(id: string) {
    setMembersLoading(true);
    api
      .listMembers(id)
      .then(setMembers)
      .catch((e) => {
        setMembers([]);
        toast({ tone: "error", message: `メンバー取得に失敗: ${e}` });
      })
      .finally(() => setMembersLoading(false));
  }

  function selectList(l: XList) {
    setSelected(l);
    setEditName(l.name);
    setEditDesc(l.description);
    setEditPrivate(l.private);
    setMembers([]);
    loadMembers(l.id);
  }

  async function doCreate() {
    if (!newName.trim()) {
      toast({ tone: "error", message: "リスト名を入力してください。" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const accounts = newAccounts
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const r = await api.createList({
        name: newName.trim(),
        accounts,
        description: newDesc.trim(),
        private: newPrivate,
      });
      toast({
        tone: "success",
        message: `「${r.name}」を作成（追加${r.added.length}件${
          r.skipped.length ? ` / スキップ${r.skipped.length}件` : ""
        }）`,
      });
      if (r.skipped.length) {
        setError(
          `スキップ: ${r.skipped.map((s) => `@${s.handle}（${s.reason}）`).join(", ")}`
        );
      }
      setCreating(false);
      setNewName("");
      setNewDesc("");
      setNewAccounts("");
      setNewPrivate(true);
      loadLists();
    } catch (e) {
      toast({ tone: "error", message: `作成に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.updateList(selected.id, {
        name: editName.trim(),
        description: editDesc,
        private: editPrivate,
      });
      toast({ tone: "success", message: "リストを更新しました。" });
      setSelected({ ...selected, name: editName.trim(), description: editDesc, private: editPrivate });
      loadLists();
    } catch (e) {
      toast({ tone: "error", message: `更新に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function doDelete() {
    if (!toDelete) return;
    setBusy(true);
    try {
      await api.deleteList(toDelete.id);
      toast({ tone: "success", message: `「${toDelete.name}」を削除しました。` });
      if (selected?.id === toDelete.id) {
        setSelected(null);
        setMembers([]);
      }
      setToDelete(null);
      loadLists();
    } catch (e) {
      toast({ tone: "error", message: `削除に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function addMember() {
    if (!selected || !addHandle.trim()) return;
    setBusy(true);
    try {
      await api.addListMember(selected.id, { handle: addHandle.trim() });
      toast({ tone: "success", message: `@${addHandle.trim().replace(/^@/, "")} を追加しました。` });
      setAddHandle("");
      loadMembers(selected.id);
      loadLists();
    } catch (e) {
      toast({ tone: "error", message: `追加に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function removeMember(m: XListMember) {
    if (!selected) return;
    try {
      await api.removeListMember(selected.id, m.id);
      setMembers((cur) => cur.filter((x) => x.id !== m.id));
      toast({ tone: "info", message: `@${m.username ?? m.id} を除外しました。` });
      loadLists();
    } catch (e) {
      toast({ tone: "error", message: `除外に失敗: ${e}` });
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">Lists</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Xネイティブの「リスト」を管理。作成・メンバー編集は公式API（自分のアカウント書込）、
            メンバー情報の取得は安価な読取API経由です。
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={loadLists} disabled={loading} variant="outline">
            {loading ? <Spinner /> : "更新"}
          </Button>
          <Button onClick={() => setCreating((v) => !v)}>{creating ? "閉じる" : "新規作成"}</Button>
        </div>
      </div>

      {error && <div className="text-sm text-amber-300">{error}</div>}

      {creating && (
        <Card className="space-y-3">
          <div className="text-sm font-medium">新しいリストを作成</div>
          <Input
            placeholder="リスト名"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <Input
            placeholder="説明（任意）"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <Textarea
            rows={4}
            placeholder="追加するアカウントのハンドル（@有無どちらでも。改行またはカンマ区切り）"
            value={newAccounts}
            onChange={(e) => setNewAccounts(e.target.value)}
          />
          <Switch
            checked={newPrivate}
            onChange={setNewPrivate}
            label="非公開リスト"
            hint="オンなら自分だけが閲覧可"
          />
          <div className="flex justify-end">
            <Button onClick={doCreate} disabled={busy}>
              {busy ? <Spinner /> : "作成する"}
            </Button>
          </div>
        </Card>
      )}

      {lists.length === 0 && !loading && (
        <div className="text-sm text-zinc-500">所有リストがありません。「新規作成」から作れます。</div>
      )}

      <div className="space-y-2">
        {lists.map((l) => (
          <Card
            key={l.id}
            className={
              "flex cursor-pointer items-center justify-between " +
              (selected?.id === l.id ? "ring-1 ring-sky-500/60" : "")
            }
          >
            <button className="flex-1 text-left" onClick={() => selectList(l)}>
              <div className="flex items-center gap-2">
                <span className="font-medium text-zinc-100">{l.name}</span>
                <Badge tone={l.private ? "zinc" : "green"}>{l.private ? "非公開" : "公開"}</Badge>
                <span className="text-xs text-zinc-500">{l.member_count}人</span>
              </div>
              {l.description && (
                <div className="mt-1 text-xs text-zinc-500">{l.description}</div>
              )}
            </button>
            <Button size="sm" variant="danger" onClick={() => setToDelete(l)}>
              削除
            </Button>
          </Card>
        ))}
      </div>

      {selected && (
        <Card className="space-y-4">
          <div className="text-sm font-medium">「{selected.name}」の設定</div>
          <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="リスト名" />
          <Input
            value={editDesc}
            onChange={(e) => setEditDesc(e.target.value)}
            placeholder="説明"
          />
          <Switch checked={editPrivate} onChange={setEditPrivate} label="非公開リスト" />
          <div className="flex justify-end">
            <Button size="sm" onClick={saveEdit} disabled={busy}>
              設定を保存
            </Button>
          </div>

          <div className="border-t border-zinc-800 pt-3">
            <div className="mb-2 flex items-center gap-2">
              <Input
                className="flex-1"
                placeholder="@handle を追加"
                value={addHandle}
                onChange={(e) => setAddHandle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addMember()}
              />
              <Button size="sm" onClick={addMember} disabled={busy || !addHandle.trim()}>
                追加
              </Button>
            </div>

            {membersLoading ? (
              <div className="py-4 text-center text-zinc-500">
                <Spinner />
              </div>
            ) : members.length === 0 ? (
              <div className="py-2 text-sm text-zinc-500">メンバーがいません。</div>
            ) : (
              <div className="space-y-1">
                {members.map((m) => (
                  <div
                    key={m.id}
                    className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-zinc-800/50"
                  >
                    {m.profile_image_url ? (
                      <img
                        src={m.profile_image_url}
                        alt=""
                        className="h-9 w-9 shrink-0 rounded-full"
                      />
                    ) : (
                      <div className="h-9 w-9 shrink-0 rounded-full bg-zinc-700" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm text-zinc-100">{m.name ?? m.username}</span>
                        <span className="shrink-0 text-xs text-zinc-500">@{m.username}</span>
                      </div>
                      {m.description && (
                        <div className="truncate text-xs text-zinc-500">{m.description}</div>
                      )}
                    </div>
                    <span className="shrink-0 text-xs text-zinc-500">
                      {m.followers_count.toLocaleString()} フォロワー
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => removeMember(m)}>
                      除外
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}

      <ConfirmDialog
        open={toDelete !== null}
        title="このリストを削除します"
        description="削除すると元に戻せません。リスト自体が削除されます（メンバーのアカウントには影響しません）。"
        confirmLabel="削除する"
        confirmVariant="danger"
        busy={busy}
        onConfirm={doDelete}
        onCancel={() => setToDelete(null)}
      >
        {toDelete && (
          <div className="rounded-md border border-zinc-800 bg-zinc-950 p-2 text-sm">
            {toDelete.name}（{toDelete.member_count}人）
          </div>
        )}
      </ConfirmDialog>
    </div>
  );
}
