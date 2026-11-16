"use client";

/**
 * 4 桁の暗証番号の入力（タブレット向けの大きなキー・入力は●で隠す）。4 桁そろったら onComplete を呼ぶ。
 * PC では数字キー・Backspace でも入力できる。
 */
import { useEffect, useState } from "react";

export function PinPad({ onComplete, disabled, resetKey }: { onComplete: (pin: string) => void; disabled?: boolean; resetKey?: number }) {
  const [pin, setPin] = useState("");

  // 間違えたときなどに呼び出し側から消す
  useEffect(() => {
    setPin("");
  }, [resetKey]);

  function press(d: string) {
    if (disabled || pin.length >= 4) return;
    // 送信の予約は状態更新の関数の外で行う（更新の関数は開発モードで 2 回呼ばれるため、中で予約すると暗証番号が 2 回送られる）
    const next = pin + d;
    setPin(next);
    if (next.length === 4) setTimeout(() => onComplete(next), 0);
  }
  function back() {
    if (!disabled) setPin((p) => p.slice(0, -1));
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^[0-9]$/.test(e.key)) {
        e.preventDefault();
        press(e.key);
      } else if (e.key === "Backspace") {
        e.preventDefault();
        back();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-center gap-4" aria-label={`${pin.length} 桁入力済み`}>
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className="inline-block rounded-full"
            style={{ width: 22, height: 22, border: "2px solid var(--color-border)", background: i < pin.length ? "var(--color-text)" : "transparent" }}
          />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2 max-w-xs mx-auto">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
          <button key={d} type="button" className="btn btn-xl" style={{ minHeight: 64 }} disabled={disabled} onClick={() => press(d)}>
            {d}
          </button>
        ))}
        <span />
        <button type="button" className="btn btn-xl" style={{ minHeight: 64 }} disabled={disabled} onClick={() => press("0")}>
          0
        </button>
        <button type="button" className="btn btn-xl" style={{ minHeight: 64 }} disabled={disabled} onClick={back} aria-label="1 文字消す">
          ⌫
        </button>
      </div>
    </div>
  );
}
