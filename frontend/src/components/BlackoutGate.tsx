import { useState } from "react";
import { api } from "../api";
import { ConfirmDialog } from "./ConfirmDialog";

type Pending = {
  reason: string;
  stage: 1 | 2;
  onProceed: (override: boolean) => void;
};

/**
 * 制限時間帯(ブラックアウト)の二段階確認ゲート。
 *
 * 使い方: 書き込み直前に gate(onProceed, at?) を呼ぶ。
 * - 制限帯でなければ即 onProceed(false)（通常実行）。
 * - 制限帯なら「⚠ 警告 → 警告を無視する → 最終確認 → 実行」を経て onProceed(true)。
 * at は予約時刻(ISO)。省略すると現在時刻で判定する。
 * 返り値の element を画面に一度描画しておくこと。
 */
export function useBlackoutGate() {
  const [pending, setPending] = useState<Pending | null>(null);

  async function gate(onProceed: (override: boolean) => void, at?: string) {
    try {
      const st = await api.blackoutStatus(at);
      if (!st.blackout) {
        onProceed(false);
        return;
      }
      setPending({ reason: st.reason, stage: 1, onProceed });
    } catch {
      // 判定取得に失敗したらそのまま実行を試みる(サーバ側ガードが最後の砦)
      onProceed(false);
    }
  }

  const element = (
    <>
      <ConfirmDialog
        open={pending?.stage === 1}
        title="⚠ 制限時間帯です"
        description={`${pending?.reason ?? ""} この時間は投稿・リポストを止めています。どうしても実行する場合のみ進んでください。`}
        confirmLabel="警告を無視する"
        confirmVariant="danger"
        onConfirm={() => setPending((p) => (p ? { ...p, stage: 2 } : null))}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.stage === 2}
        title="最終確認"
        description="制限時間帯ですが、本当に実行しますか？この操作は取り消せません。"
        confirmLabel="実行する"
        confirmVariant="danger"
        onConfirm={() => {
          const p = pending;
          setPending(null);
          p?.onProceed(true);
        }}
        onCancel={() => setPending(null)}
      />
    </>
  );

  return { gate, element };
}
