"use client";

import { useState } from "react";
import { yen } from "@/lib/format";
import type { Employee } from "@/lib/types";
import { isOnShift, SHIFT_LABEL } from "./ShiftGuard";

export type SplitShare = { employee: number; amount: string };

/**
 * キャスト折半: 注文画面に重ねるパネル。複数人にチェックし、バック（円）を手で配分する。
 * ここで決めるのはバックの配分だけで、販売額（お客様への請求）は変わらない。ポイント欄は出さない（UNIFIED_PLAN 6 章 Q10）。
 * 配分の合計が明細の金額を超えていないか・同じ人の重複は、確定のときにサーバーが検証する。
 */
export function SplitBackPanel({
  productName,
  unitPrice,
  casts,
  initial,
  guard,
  onCancel,
  onDone,
}: {
  productName: string;
  unitPrice: string;
  casts: Employee[];
  initial: SplitShare[];
  guard: (e: Employee) => Promise<boolean>;
  onCancel: () => void;
  onDone: (shares: SplitShare[]) => void;
}) {
  const [shares, setShares] = useState<SplitShare[]>(initial);
  const [focus, setFocus] = useState<number | null>(initial[0]?.employee ?? null);
  const checked = (id: number) => shares.some((s) => s.employee === id);
  const sum = shares.reduce((s, x) => s + (Number(x.amount) || 0), 0);
  const press = (k: string) => {
    if (focus === null) return;
    setShares((ss) =>
      ss.map((s) => {
        if (s.employee !== focus) return s;
        if (k === "C") return { ...s, amount: "" };
        if (k === "⌫") return { ...s, amount: s.amount.slice(0, -1) };
        const next = (s.amount + k).replace(/^0+(?=\d)/, "");
        return next.length > 7 ? s : { ...s, amount: next };
      }),
    );
  };

  async function toggle(e: Employee) {
    if (checked(e.id)) {
      setShares((ss) => ss.filter((s) => s.employee !== e.id));
      setFocus((f) => (f === e.id ? null : f));
      return;
    }
    if (!(await guard(e))) return;
    setShares((ss) => [...ss, { employee: e.id, amount: "" }]);
    setFocus(e.id);
  }

  const name = (id: number) => casts.find((c) => c.id === id)?.display_name ?? "?";
  const ready = shares.length >= 2 && shares.every((s) => s.amount !== "");

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/60" role="dialog" aria-modal="true" aria-label="キャスト折半">
      <div className="h-full flex flex-col p-3 gap-2" style={{ width: "min(880px, 96vw)", paddingTop: "calc(0.75rem + var(--safe-top))", background: "var(--color-panel)", borderLeft: "2px solid var(--color-edge)" }}>
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-2xl font-bold">キャスト折半</h2>
            <div className="text-base" style={{ color: "var(--color-muted)" }}>
              {productName}（販売 {yen(unitPrice)}）のバックを、誰にいくら付けるかを決めます。販売額は変わりません。
            </div>
          </div>
          <button className="btn" onClick={onCancel} aria-label="折半をやめる">
            ✕ やめる
          </button>
        </div>
        <div className="grid grid-cols-12 gap-2 flex-1 min-h-0">
          <section className="col-span-4 overflow-y-auto space-y-1 pr-1" aria-label="折半するキャスト">
            {casts.map((e) => {
              const on = checked(e.id);
              return (
                <button key={e.id} className={`w-full flex items-center gap-2 rounded-xl px-3 text-left ${on ? "pos-selected" : ""}`} style={{ minHeight: 52, ...(on ? {} : { background: "var(--color-panel2)", border: "1px solid var(--color-border)", opacity: isOnShift(e) ? 1 : 0.65 }) }} onClick={() => toggle(e)} role="checkbox" aria-checked={on} aria-label={e.display_name}>
                  <span className="text-2xl w-7">{on ? "☑" : "☐"}</span>
                  <span className="text-lg font-bold flex-1">{e.display_name}</span>
                  {e.shift_status && <span className="text-xs">{SHIFT_LABEL[e.shift_status]}</span>}
                </button>
              );
            })}
          </section>
          <section className="col-span-4 overflow-y-auto space-y-2" aria-label="バックの配分">
            {shares.length === 0 && <p style={{ color: "var(--color-muted)" }}>左でキャストを 2 人以上選んでください。</p>}
            {shares.map((s) => (
              <button key={s.employee} className="w-full rounded-xl px-3 py-2 text-left" style={{ background: "var(--color-panel2)", border: `3px solid ${focus === s.employee ? "var(--color-accent)" : "var(--color-border)"}` }} onClick={() => setFocus(s.employee)} aria-pressed={focus === s.employee} aria-label={`${name(s.employee)} のバック`}>
                <div className="text-base font-bold">
                  {name(s.employee)}
                  {focus === s.employee && <span className="font-normal"> ← 入力中</span>}
                </div>
                <div className="text-3xl font-bold num text-right">{s.amount === "" ? "¥—" : yen(s.amount)}</div>
              </button>
            ))}
            {shares.length > 0 && (
              <div className="flex justify-between items-baseline px-1">
                <span>バックの合計</span>
                <span className="text-2xl font-bold num" data-testid="split-sum">
                  {yen(sum)}
                </span>
              </div>
            )}
          </section>
          <section className="col-span-4" aria-label="金額の入力">
            <div className="grid grid-cols-3 gap-2">
              {["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", "00", "⌫"].map((k) => (
                <button key={k} type="button" className="btn text-xl num" onClick={() => press(k)} disabled={focus === null}>
                  {k}
                </button>
              ))}
            </div>
            <button type="button" className="btn w-full mt-2" onClick={() => press("C")} disabled={focus === null}>
              クリア
            </button>
            <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
              配分の合計は、この注文の販売額までです（超えると確定のときにお知らせします）。
            </p>
          </section>
        </div>
        <div className="flex justify-end gap-2 pt-2 border-t" style={{ borderColor: "var(--color-border)" }}>
          <button className="btn btn-xl" onClick={onCancel}>
            やめる
          </button>
          <button className="btn btn-xl btn-primary" style={{ minWidth: 240 }} disabled={!ready} onClick={() => onDone(shares)}>
            {shares.length < 2 ? "2 人以上選んでください" : !ready ? "金額を入れてください" : "この配分で注文に入れる"}
          </button>
        </div>
      </div>
    </div>
  );
}
