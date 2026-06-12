import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, Input, Spinner, Textarea } from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import type { PromptTemplate, TemplateKind } from "../types";

const KINDS: { key: TemplateKind; label: string }[] = [
  { key: "post", label: "投稿" },
  { key: "reply", label: "リプライ" },
  { key: "quote", label: "引用RT" },
  { key: "news", label: "ニュース速報" },
];
const KIND_LABEL: Record<TemplateKind, string> = {
  post: "投稿",
  reply: "リプライ",
  quote: "引用RT",
  news: "ニュース速報",
};

export default function Templates() {
  const toast = useToast();
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [toDelete, setToDelete] = useState<PromptTemplate | null>(null);

  // 編集中フィールド
  const [name, setName] = useState("");
  const [body, setBody] = useState("");

  // 新規作成フォーム
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState<TemplateKind>("post");
  const [newBody, setNewBody] = useState("");

  function reload() {
    setLoading(true);
    api
      .templates()
      .then(setItems)
      .catch((e) => toast({ tone: "error", message: `型の取得に失敗: ${e}` }))
      .finally(() => setLoading(false));
  }
  useEffect(reload, []);

  const selected = useMemo(
    () => items.find((t) => t.id === selectedId) ?? null,
    [items, selectedId]
  );

  function select(t: PromptTemplate) {
    setSelectedId(t.id);
    setName(t.name);
    setBody(t.body);
  }

  async function save() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.updateTemplate(selected.id, { name, body });
      toast({ tone: "success", message: "型を保存しました。" });
      reload();
    } catch (e) {
      toast({ tone: "error", message: `保存に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function activate(t: PromptTemplate) {
    setBusy(true);
    try {
      await api.activateTemplate(t.id);
      toast({ tone: "success", message: `「${t.name}」を既定にしました（${KIND_LABEL[t.kind]}）。` });
      reload();
    } catch (e) {
      toast({ tone: "error", message: `既定化に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    if (!newName.trim()) {
      toast({ tone: "error", message: "型の名前を入力してください。" });
      return;
    }
    setBusy(true);
    try {
      const t = await api.createTemplate({ name: newName.trim(), kind: newKind, body: newBody });
      toast({ tone: "success", message: `型「${t.name}」を作成しました。` });
      setNewName("");
      setNewBody("");
      const fresh = await api.templates();
      setItems(fresh);
      select(t);
    } catch (e) {
      toast({ tone: "error", message: `作成に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!toDelete) return;
    setBusy(true);
    try {
      await api.deleteTemplate(toDelete.id);
      toast({ tone: "info", message: `「${toDelete.name}」を削除しました。` });
      if (selectedId === toDelete.id) setSelectedId(null);
      setToDelete(null);
      reload();
    } catch (e) {
      toast({ tone: "error", message: `削除に失敗: ${e}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Templates</h1>
        <p className="mt-1 text-sm text-zinc-500">
          投稿/リプの「型」を保存します。Composeで型を選ぶか「AIに任せる」でAIが最適な型を自動選択します。
          各カテゴリの「既定」はInboxの自動リプ/絡みにも使われます。
        </p>
      </div>

      <Card className="space-y-3">
        <div className="text-sm text-zinc-400">新しい型を作る</div>
        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
          <Input placeholder="型の名前（例: 速報の型）" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <select
            value={newKind}
            onChange={(e) => setNewKind(e.target.value as TemplateKind)}
            className="h-9 rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
          >
            {KINDS.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <Textarea
          rows={4}
          placeholder="この型でどう書くか（狙い・テンプレ・掴み・ガードレールなど）。AIへの指示文として使われます。"
          value={newBody}
          onChange={(e) => setNewBody(e.target.value)}
        />
        <div className="flex justify-end">
          <Button onClick={create} disabled={busy || !newName.trim()}>
            {busy ? <Spinner /> : "型を作成"}
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* 一覧 */}
        <div className="space-y-2">
          {loading ? (
            <Spinner />
          ) : items.length === 0 ? (
            <div className="text-sm text-zinc-500">まだ型がありません。上で作成してください。</div>
          ) : (
            KINDS.map((k) => {
              const group = items.filter((t) => t.kind === k.key);
              if (group.length === 0) return null;
              return (
                <div key={k.key} className="space-y-1">
                  <div className="px-1 text-xs uppercase tracking-wide text-zinc-500">{k.label}</div>
                  {group.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => select(t)}
                      className={
                        "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors " +
                        (selectedId === t.id
                          ? "border-sky-500 bg-sky-500/10"
                          : "border-zinc-800 hover:bg-zinc-800/60")
                      }
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-zinc-100">{t.name}</span>
                        {t.active && <Badge tone="green">既定</Badge>}
                        {t.builtin && <Badge tone="zinc">内蔵</Badge>}
                      </div>
                    </button>
                  ))}
                </div>
              );
            })
          )}
        </div>

        {/* 編集 */}
        <Card className="space-y-3">
          {!selected ? (
            <div className="text-sm text-zinc-500">左の一覧から型を選ぶと編集できます。</div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="sky">{KIND_LABEL[selected.kind]}</Badge>
                {selected.active && <Badge tone="green">既定</Badge>}
                {selected.builtin && <Badge tone="zinc">内蔵（編集可）</Badge>}
              </div>
              <label className="block space-y-1">
                <span className="text-xs text-zinc-500">名前</span>
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="block space-y-1">
                <span className="text-xs text-zinc-500">本文（AIへの指示文）</span>
                <Textarea rows={14} value={body} onChange={(e) => setBody(e.target.value)} />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button onClick={save} disabled={busy}>
                  {busy ? <Spinner /> : "保存"}
                </Button>
                {!selected.active && (
                  <Button variant="outline" onClick={() => activate(selected)} disabled={busy}>
                    これを既定にする
                  </Button>
                )}
                <Button variant="danger" onClick={() => setToDelete(selected)} disabled={busy}>
                  削除
                </Button>
              </div>
            </>
          )}
        </Card>
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title="型を削除しますか？"
        description={toDelete ? `「${toDelete.name}」を削除します。元に戻せません。` : undefined}
        confirmLabel="削除する"
        busy={busy}
        onConfirm={remove}
        onCancel={() => setToDelete(null)}
      />
    </div>
  );
}
