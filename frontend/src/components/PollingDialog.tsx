"use client";

/**
 * サーバーの処理（AI 読取・CSV 解析など）が終わるまで数秒ごとに状態を確認するダイアログ。
 * 待っている間は閉じられない。通信が切れたら表示し、失敗したら「手で入力する」などの代わりの操作を出す。
 */
import { useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { Modal } from "./ui";

export function PollingDialog<T>({
  title,
  path,
  isDone,
  isFailed,
  onDone,
  onFailed,
  intervalMs = 2000,
  message = "処理しています。少しお待ちください…",
  timeoutMs = 90000,
}: {
  title: string;
  path: string;
  isDone: (t: T) => boolean;
  isFailed?: (t: T) => boolean;
  onDone: (t: T) => void;
  onFailed?: (t: T | null, reason: string) => void;
  intervalMs?: number;
  message?: string;
  timeoutMs?: number;
}) {
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [started] = useState(() => Date.now());

  useEffect(() => {
    let stop = false;
    const timer = setTimeout(async () => {
      try {
        const t = await api<T>(path);
        if (stop) return;
        setOffline(false);
        if (isDone(t)) return onDone(t);
        if (isFailed?.(t)) return onFailed?.(t, "failed");
        if (Date.now() - started > timeoutMs) return onFailed?.(t, "timeout");
        setTick((n) => n + 1);
      } catch (e) {
        if (stop) return;
        if (e instanceof ApiError && e.code === "network") setOffline(true);
        else setError(errorMessage(e));
        setTick((n) => n + 1);
      }
    }, intervalMs);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, path]);

  return (
    <Modal title={title}>
      <div className="flex items-center gap-3 py-4">
        <span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent" />
        <span className="text-lg">{message}</span>
      </div>
      {offline && <p className="badge badge-danger">通信できません。接続を確認しています…</p>}
      {error && <p className="badge badge-warn">{error}（もう一度確認します）</p>}
      {onFailed && (
        <div className="mt-4">
          <button className="btn" onClick={() => onFailed(null, "skipped")}>
            待たずに手で入力する
          </button>
        </div>
      )}
    </Modal>
  );
}
