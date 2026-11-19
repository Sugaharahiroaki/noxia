"use client";

import { useEffect, useRef } from "react";

const ITEM = 96; // 1 つの値の幅（px）。真ん中に来た値が選ばれた値

/**
 * 横にスワイプして値を選ぶ（入店画面のセットの時間・料金）。
 * - 指で横に動かすと、止まったところで真ん中の値が選ばれる（スナップする）
 * - となりに見えている値をタップしても、その値になる
 * 値はそのままサーバーに送る（金額の計算はサーバーの見積で行う）。
 */
export function SwipePicker({
  label,
  values,
  value,
  onChange,
  format,
  caption,
  testId,
}: {
  label: string;
  values: number[];
  value: number;
  onChange: (v: number) => void;
  format: (v: number) => string;
  /** 値の下に小さく出す説明（例: 0 分 → 無制限） */
  caption?: (v: number) => string | null;
  testId?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const latest = useRef({ values, value, onChange });
  latest.current = { values, value, onChange };

  // 値が外から変わったとき（セットを選び直したなど）・最初の表示で、その値を真ん中へ
  useEffect(() => {
    const el = ref.current;
    const i = values.indexOf(value);
    if (!el || i < 0) return;
    if (Math.abs(el.scrollLeft - i * ITEM) > 2) el.scrollTo({ left: i * ITEM, behavior: "auto" });
  }, [value, values]);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  function onScroll() {
    if (timer.current) window.clearTimeout(timer.current);
    // 指を離してスナップし終わったところで決める
    timer.current = window.setTimeout(() => {
      const el = ref.current;
      if (!el) return;
      const { values: vs, value: cur, onChange: change } = latest.current;
      const i = Math.max(0, Math.min(vs.length - 1, Math.round(el.scrollLeft / ITEM)));
      if (vs[i] !== cur) change(vs[i]);
    }, 140);
  }

  function pick(i: number) {
    ref.current?.scrollTo({ left: i * ITEM, behavior: "smooth" });
    if (values[i] !== value) onChange(values[i]);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    const i = values.indexOf(value);
    if (e.key === "ArrowLeft" && i > 0) {
      e.preventDefault();
      pick(i - 1);
    } else if (e.key === "ArrowRight" && i < values.length - 1) {
      e.preventDefault();
      pick(i + 1);
    }
  }

  return (
    <div className="relative select-none" data-testid={testId}>
      {/* 真ん中の枠（ここに来た値が選ばれた値） */}
      <div
        aria-hidden
        className="pointer-events-none absolute top-1 bottom-1 left-1/2 -translate-x-1/2 rounded-xl"
        style={{ width: ITEM - 6, background: "var(--color-panel3)", border: "2px solid var(--color-edge)" }}
      />
      <div
        ref={ref}
        role="group"
        aria-label={`${label}（横にスワイプして選ぶ。いま ${format(value)}）`}
        tabIndex={0}
        onScroll={onScroll}
        onKeyDown={onKeyDown}
        className="relative flex overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        style={{ scrollSnapType: "x mandatory", WebkitOverflowScrolling: "touch", maskImage: "linear-gradient(to right, transparent, #000 18%, #000 82%, transparent)" }}
      >
        <div aria-hidden style={{ flex: `0 0 calc(50% - ${ITEM / 2}px)` }} />
        {values.map((v, i) => {
          const selected = v === value;
          const cap = caption?.(v);
          return (
            <button
              key={v}
              type="button"
              tabIndex={-1}
              className="flex flex-col items-center justify-center num"
              style={{ flex: `0 0 ${ITEM}px`, height: 64, scrollSnapAlign: "center", color: selected ? "var(--color-text)" : "var(--color-muted)", opacity: selected ? 1 : 0.6 }}
              onClick={() => pick(i)}
              aria-pressed={selected}
              aria-label={`${label} ${format(v)}${cap ? `（${cap}）` : ""}`}
            >
              <span className={selected ? "text-2xl font-bold" : "text-lg"}>{format(v)}</span>
              {cap && <span className="text-xs font-semibold">{cap}</span>}
            </button>
          );
        })}
        <div aria-hidden style={{ flex: `0 0 calc(50% - ${ITEM / 2}px)` }} />
      </div>
    </div>
  );
}
