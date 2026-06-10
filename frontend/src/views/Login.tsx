import { useState } from "react";
import { api, setToken } from "../api";
import { Button, Card, Input, Spinner } from "../components/ui";

/** API_TOKEN 認証のログイン画面。サーバが 401 を返したときだけ表示される。 */
export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    setToken(value.trim());
    try {
      // /status は認証必須かつ X API を呼ばない軽いエンドポイント=トークン検証に使う。
      await api.status();
      onSuccess();
    } catch (e) {
      setError(
        e instanceof Error && e.message.startsWith("401")
          ? "トークンが違います。"
          : `接続エラー: ${e instanceof Error ? e.message : e}`
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <div className="text-lg font-semibold tracking-tight">XAgent</div>
        <div className="mt-1 text-xs text-zinc-500">
          アクセストークンを入力してください（運用者から共有された値）。
        </div>
        <div className="mt-4 space-y-3">
          <Input
            type="password"
            placeholder="アクセストークン"
            value={value}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {error && <div className="text-xs text-red-400">{error}</div>}
          <Button className="w-full" disabled={!value.trim() || busy} onClick={submit}>
            {busy ? <Spinner /> : "ログイン"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
