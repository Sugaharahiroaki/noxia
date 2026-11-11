"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export type ToastAction = { label: string; onClick: () => Promise<void> | void };
export type ToastInput = { message: string; tone?: "ok" | "error" | "info"; action?: ToastAction; durationMs?: number };

type ToastState = ToastInput & { id: number; createdAt: number };

const ToastContext = createContext<{ show: (t: ToastInput) => void; hide: () => void }>({ show: () => {}, hide: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

/** 画面遷移しても消えない大きな通知。「取り消す」などの操作ボタンを1つ付けられる。 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);
  const seq = useRef(0);

  const hide = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    setToast(null);
  }, []);

  const show = useCallback((t: ToastInput) => {
    if (timer.current) window.clearTimeout(timer.current);
    const id = ++seq.current;
    setToast({ ...t, id, createdAt: Date.now() });
    setBusy(false);
    const ms = t.durationMs ?? (t.action ? 10000 : t.tone === "error" ? 8000 : 3500);
    timer.current = window.setTimeout(() => setToast((cur) => (cur && cur.id === id ? null : cur)), ms);
  }, []);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  // 通知を出した直後の画面遷移（例: 注文確定 → 卓一覧）では残し、
  // その後に別の画面へ移ったら消す（次の画面のボタンに重ならないように）
  const pathname = usePathname();
  const toastRef = useRef<ToastState | null>(null);
  toastRef.current = toast;
  useEffect(() => {
    const t = toastRef.current;
    if (t && Date.now() - t.createdAt > 1500) hide();
  }, [pathname, hide]);

  return (
    <ToastContext.Provider value={{ show, hide }}>
      {children}
      {toast && (
        <div className={`toast toast-${toast.tone ?? "ok"}`} role={toast.tone === "error" ? "alert" : "status"} aria-live="polite">
          <span aria-hidden>{toast.tone === "error" ? "✕" : toast.tone === "info" ? "ℹ" : "✓"}</span>
          <span className="flex-1">{toast.message}</span>
          {toast.action && (
            <button
              className="btn btn-lg"
              style={{ background: "#fff", color: "#111", borderColor: "#fff" }}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await toast.action!.onClick();
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "処理中…" : toast.action.label}
            </button>
          )}
          <button className="btn btn-sm btn-ghost" onClick={hide} aria-label="通知を閉じる" style={{ color: "#fff" }}>
            ✕
          </button>
        </div>
      )}
    </ToastContext.Provider>
  );
}
