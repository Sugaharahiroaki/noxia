"use client";

import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";

export function Spinner({ label = "読み込み中…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-6" style={{ color: "var(--color-muted)" }} role="status">
      <span className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="card-2 flex items-start gap-3" style={{ borderColor: "var(--color-danger)" }} role="alert">
      <span className="text-xl" aria-hidden>
        ⚠
      </span>
      <div className="flex-1">
        <p className="font-semibold">{errorMessage(error)}</p>
      </div>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          再読み込み
        </button>
      )}
    </div>
  );
}

export type SaveState = { kind: "idle" } | { kind: "saving"; label?: string } | { kind: "saved"; label?: string } | { kind: "error"; error: unknown };

/** 通信中・保存済み・保存失敗を文字と記号で明確に区別して表示する */
export function SaveStatus({ state }: { state: SaveState }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    setVisible(true);
    if (state.kind === "saved") {
      const t = setTimeout(() => setVisible(false), 2500);
      return () => clearTimeout(t);
    }
  }, [state]);
  if (state.kind === "idle" || !visible) return null;
  if (state.kind === "saving")
    return (
      <span className="badge badge-accent" role="status">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
        {state.label ?? "通信中…"}
      </span>
    );
  if (state.kind === "saved")
    return (
      <span className="badge badge-ok" role="status">
        ✓ {state.label ?? "保存しました"}
      </span>
    );
  return (
    <span className="badge badge-danger" role="alert">
      ✕ 保存できませんでした: {errorMessage(state.error)}
    </span>
  );
}

export function Modal({
  title,
  children,
  onClose,
  wide,
  footer,
}: {
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
  wide?: boolean;
  footer?: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className={`card w-full ${wide ? "max-w-4xl" : "max-w-xl"} max-h-[92vh] flex flex-col`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xl font-bold">{title}</h2>
          {onClose && (
            <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="閉じる">
              ✕
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto pr-1">{children}</div>
        {footer && <div className="pt-3 mt-3 border-t flex gap-3 justify-end" style={{ borderColor: "var(--color-border)" }}>{footer}</div>}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-4">
      <div>
        <h1 className="text-2xl font-bold">{title}</h1>
        {subtitle && (
          <div className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            {subtitle}
          </div>
        )}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && (
        <span className="block text-xs mt-1" style={{ color: "var(--color-muted)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-3 min-h-[52px] px-3 rounded-xl w-full text-left"
      style={{ background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}
    >
      <span
        className="relative inline-block h-7 w-12 rounded-full transition"
        style={{ background: checked ? "var(--color-ok)" : "var(--color-panel3)" }}
      >
        <span className="absolute top-1 h-5 w-5 rounded-full bg-white transition" style={{ left: checked ? 26 : 4 }} />
      </span>
      <span>
        {label} <span className="text-sm" style={{ color: "var(--color-muted)" }}>({checked ? "ON" : "OFF"})</span>
      </span>
    </button>
  );
}

/** 金額入力用テンキー */
// いま画面に出ているテンキーのうち、最後に出たもの（一番手前）だけがキーボード入力を受け取る
const padStack: number[] = [];
let padSeq = 0;

/**
 * 金額入力用テンキー（タッチ・クリック・キーボード）。
 * キーボード: 数字キー＝入力、Backspace＝1文字消す、Delete＝全部消す、Enter＝onEnterKey（なければ onEnter）。
 * 文字入力欄にカーソルがあるとき、手前に別のダイアログが開いているときはキーを受け取らない。
 */
export function NumberPad({
  value,
  onChange,
  onEnter,
  onEnterKey,
  quick,
}: {
  value: string;
  onChange: (v: string) => void;
  onEnter?: () => void;
  onEnterKey?: () => void;
  quick?: number[];
}) {
  const press = (k: string) => {
    if (k === "C") return onChange("");
    if (k === "⌫") return onChange(value.slice(0, -1));
    if (k === "00" && value === "") return;
    const next = (value + k).replace(/^0+(?=\d)/, "");
    if (next.length > 9) return;
    onChange(next);
  };
  const rootRef = useRef<HTMLDivElement>(null);
  const latest = useRef({ press, onEnter, onEnterKey });
  latest.current = { press, onEnter, onEnterKey };

  useEffect(() => {
    const id = ++padSeq;
    padStack.push(id);
    const onKey = (e: KeyboardEvent) => {
      if (padStack[padStack.length - 1] !== id) return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      // 手前に別のダイアログがあり、このテンキーがその中にない → 受け取らない
      const dialogs = document.querySelectorAll('[role="dialog"]');
      const top = dialogs[dialogs.length - 1];
      if (top && rootRef.current && !top.contains(rootRef.current)) return;
      const { press: p, onEnter: enter, onEnterKey: enterKey } = latest.current;
      if (/^[0-9]$/.test(e.key)) {
        p(e.key);
        e.preventDefault();
      } else if (e.key === "Backspace") {
        p("⌫");
        e.preventDefault();
      } else if (e.key === "Delete") {
        p("C");
        e.preventDefault();
      } else if (e.key === "Enter" && (enterKey || enter)) {
        (enterKey || enter)!();
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const i = padStack.indexOf(id);
      if (i >= 0) padStack.splice(i, 1);
    };
  }, []);

  return (
    <div className="space-y-2" ref={rootRef}>
      {quick && quick.length > 0 && (
        <div className="grid grid-cols-4 gap-2">
          {quick.map((q) => (
            <button key={q} type="button" className="btn btn-sm num" onClick={() => onChange(String(q))}>
              {q.toLocaleString("ja-JP")}
            </button>
          ))}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        {["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", "00", "⌫"].map((k) => (
          <button key={k} type="button" className="btn text-xl num" onClick={() => press(k)}>
            {k}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn" onClick={() => press("C")}>
          クリア
        </button>
        {onEnter && (
          <button type="button" className="btn btn-primary" onClick={onEnter}>
            決定
          </button>
        )}
      </div>
      <p className="kbd-hint text-sm" style={{ color: "var(--color-muted)" }}>
        キーボード: 数字で入力 ・ Backspace で1文字消す ・ Delete でクリア{onEnter || onEnterKey ? " ・ Enter で次へ" : ""}
      </p>
    </div>
  );
}

export function Stat({ label, value, sub, accent }: { label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: "ok" | "warn" | "danger" }) {
  const color = accent === "ok" ? "var(--color-ok)" : accent === "warn" ? "var(--color-warn)" : accent === "danger" ? "var(--color-danger)" : "var(--color-text)";
  return (
    <div className="card-2">
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        {label}
      </div>
      <div className="text-2xl font-bold num" style={{ color }}>
        {value}
      </div>
      {sub && (
        <div className="text-xs mt-1" style={{ color: "var(--color-muted)" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="py-8 text-center" style={{ color: "var(--color-muted)" }}>
      {children}
    </div>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "実行する",
  danger,
  onConfirm,
  onCancel,
  busy,
  children,
}: {
  title: string;
  message?: React.ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Modal
      title={title}
      onClose={busy ? undefined : onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel} disabled={busy}>
            キャンセル
          </button>
          <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>
            {busy ? "処理中…" : confirmLabel}
          </button>
        </>
      }
    >
      {message && <p className="mb-3">{message}</p>}
      {children}
    </Modal>
  );
}
