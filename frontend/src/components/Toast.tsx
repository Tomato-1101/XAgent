import { createContext, useCallback, useContext, useRef, useState } from "react";

type Tone = "success" | "error" | "info";

type Toast = {
  id: number;
  tone: Tone;
  message: string;
  href?: string;
  linkLabel?: string;
};

type ToastInput = Omit<Toast, "id">;

const ToastCtx = createContext<(t: ToastInput) => void>(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

const TONE_CLASS: Record<Tone, string> = {
  success: "border-green-700/70 bg-green-950/80 text-green-200",
  error: "border-red-800/70 bg-red-950/80 text-red-200",
  info: "border-zinc-700 bg-zinc-900 text-zinc-200",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (t: ToastInput) => {
      const id = ++seq.current;
      setToasts((prev) => [...prev, { ...t, id }]);
      // 成功/情報は自動で消す。エラーは手動で閉じるまで残す。
      if (t.tone !== "error") {
        setTimeout(() => remove(id), 8000);
      }
    },
    [remove]
  );

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className={`rounded-lg border p-3 text-sm shadow-lg ${TONE_CLASS[t.tone]}`}>
            <div className="flex items-start justify-between gap-2">
              <span className="whitespace-pre-wrap">{t.message}</span>
              <button
                onClick={() => remove(t.id)}
                className="shrink-0 text-zinc-400 hover:text-zinc-200"
                aria-label="閉じる"
              >
                ×
              </button>
            </div>
            {t.href && (
              <a
                href={t.href}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-sky-300 underline hover:text-sky-200"
              >
                {t.linkLabel ?? t.href}
              </a>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
