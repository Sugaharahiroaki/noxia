"use client";

import { useCallback, useState } from "react";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Employee } from "@/lib/types";
import { Modal } from "./ui";

export const SHIFT_LABEL: Record<string, string> = { working: "出勤中", on_break: "休憩中", left: "退勤済み", off: "未出勤" };

export function isOnShift(e: Employee): boolean {
  return e.shift_status === "working" || e.shift_status === "on_break";
}

/** 出勤中の人を先に並べる */
export function sortByShift(list: Employee[]): Employee[] {
  const rank = (e: Employee) => (e.shift_status === "working" ? 0 : e.shift_status === "on_break" ? 1 : e.shift_status === "off" ? 2 : 3);
  return [...list].sort((a, b) => rank(a) - rank(b));
}

/** 名前ボタン（下に出勤状態を小さく出す。未出勤・退勤済みは少し暗く） */
export function StaffButton({ e, selected, onClick, size = "xl" }: { e: Employee; selected: boolean; onClick: () => void; size?: "xl" | "md" }) {
  const on = isOnShift(e);
  return (
    <button
      type="button"
      className={`btn ${size === "xl" ? "btn-xl" : ""} flex-col h-auto py-2 ${selected ? "btn-primary" : ""}`}
      style={!selected && !on ? { opacity: 0.6, borderStyle: "dashed" } : undefined}
      onClick={onClick}
      aria-pressed={selected}
    >
      <span>{e.display_name}</span>
      {e.shift_status && (
        <span className="text-sm font-normal" style={{ color: selected ? "#fff" : on ? "var(--color-ok)" : "var(--color-muted)" }}>
          {SHIFT_LABEL[e.shift_status]}
        </span>
      )}
    </button>
  );
}

/**
 * 出勤していない人を担当・バック対象に選ぼうとしたときの確認。
 * guard(e) が true を返したら選んでよい。店長・オーナーはその場で「出勤にして選ぶ」ことができる。
 */
export function useShiftGuard(onClockedIn?: () => void) {
  const { can } = useSession();
  const [pending, setPending] = useState<{ emp: Employee; resolve: (ok: boolean) => void } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const guard = useCallback((emp: Employee): Promise<boolean> => {
    if (!emp.shift_status || isOnShift(emp)) return Promise.resolve(true);
    setError(null);
    return new Promise<boolean>((resolve) => setPending({ emp, resolve }));
  }, []);

  const finish = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };

  const canClockIn = can("attendance.manage");
  const element = pending ? (
    <Modal title={`${pending.emp.display_name}さんは${pending.emp.shift_status === "left" ? "退勤済みです" : "まだ出勤していません"}`} onClose={busy ? undefined : () => finish(false)}>
      <div className="space-y-4">
        <p className="text-xl">
          {pending.emp.shift_status === "left"
            ? `${fmtTime(pending.emp.shift_since)} に退勤しています。席に着いている場合は、もう一度出勤にしてください。`
            : "出勤を押していないようです。席に着いているなら、出勤の押し忘れかもしれません。"}
        </p>
        <div className="grid gap-3">
          {canClockIn && (
            <button
              className="btn btn-xl btn-ok"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await api("/api/staff/shifts/clock-in", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { employee_id: pending.emp.id } });
                  onClockedIn?.();
                  finish(true);
                } catch (e) {
                  setError(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "記録中…" : `${pending.emp.shift_status === "left" ? "もう一度出勤にして" : "いま出勤にして"}選ぶ`}
            </button>
          )}
          <button className="btn btn-xl" disabled={busy} onClick={() => finish(true)}>
            出勤にしないで、このまま選ぶ
          </button>
          <button className="btn btn-xl btn-ghost" disabled={busy} onClick={() => finish(false)}>
            やめる
          </button>
          {!canClockIn && (
            <p className="text-base" style={{ color: "var(--color-muted)" }}>
              ほかの人の出勤は店長が「出勤・お渡し」から押せます。
            </p>
          )}
          {error && (
            <p className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </p>
          )}
        </div>
      </div>
    </Modal>
  ) : null;

  return { guard, element };
}
