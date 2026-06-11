import { useEffect, useRef } from "react";

/**
 * 一覧の自動更新: ポーリング(既定30秒)+タブ復帰(visibilitychange)時の即時更新。
 * スマホは生成ジョブ完了やバックグラウンド復帰でデータが古くなるため、
 * 手動リロードなしで最新が見えるようにする。非表示タブではポーリングしない。
 */
export function useAutoRefresh(refresh: () => void, intervalMs = 30000) {
  // 最新のクロージャ(タブ切替state等に依存)を呼ぶための ref
  const ref = useRef(refresh);
  ref.current = refresh;
  useEffect(() => {
    const tick = () => {
      if (!document.hidden) ref.current();
    };
    const id = setInterval(tick, intervalMs);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [intervalMs]);
}
