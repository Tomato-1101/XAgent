import { useEffect, useRef } from "react";
import { Button, Spinner } from "./ui";

type Props = {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  confirmVariant?: "default" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
};

/**
 * 誤投稿を防ぐための確認ダイアログ。
 * - 既定フォーカスはキャンセル側（Enter連打で送信されないように）。
 * - 背景クリック / Escape はキャンセル（確認ではない）。
 * - 装飾アニメーションは入れない。
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "実行",
  confirmVariant = "default",
  busy = false,
  onConfirm,
  onCancel,
  children,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
      role="dialog"
      aria-modal="true"
    >
      {/* max-h+scroll: スマホ縦画面で時間指定予約(SchedulePicker)等が画面外にあふれないように */}
      <div className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-900 p-5 shadow-xl">
        <div className="text-base font-semibold text-zinc-100">{title}</div>
        {description && <div className="mt-1 text-sm text-zinc-400">{description}</div>}
        {children && <div className="mt-3">{children}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} variant="outline" onClick={onCancel} disabled={busy}>
            キャンセル
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={busy}>
            {busy ? <Spinner /> : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
