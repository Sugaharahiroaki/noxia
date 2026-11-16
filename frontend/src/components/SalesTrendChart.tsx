"use client";

import { useState } from "react";
import { amt, dayLabel } from "./ReportKit";

/**
 * 売上推移（1 か月の日ごとの売上）。ライブラリを使わず SVG で描く棒グラフ。
 * 値はサーバーの集計をそのまま使う。棒に触れる／フォーカスすると、その日の金額を下に出す。同じ内容は月次の表でも見られる。
 */
export function SalesTrendChart({ rows, valueKey = "sales", label = "売上" }: { rows: { date: string; sales: string; gross_profit: string }[]; valueKey?: "sales" | "gross_profit"; label?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = rows.map((r) => Number(r[valueKey]) || 0);
  const max = Math.max(1, ...values);
  const W = 720;
  const H = 200;
  const padL = 56;
  const padB = 22;
  const padT = 8;
  const innerW = W - padL - 8;
  const innerH = H - padB - padT;
  const step = innerW / Math.max(rows.length, 1);
  const barW = Math.max(3, step - 4);
  // 目盛り: 最大値を切りのよい値に上げて 4 等分
  const pow = 10 ** Math.floor(Math.log10(max));
  const top = Math.ceil(max / pow) * pow;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => top * t);
  const y = (v: number) => padT + innerH - (Math.max(v, 0) / top) * innerH;
  const shown = hover !== null ? rows[hover] : null;

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}の推移（日ごと）`} style={{ width: "100%", height: "auto", maxHeight: 260 }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - 8} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
            <text x={padL - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--color-muted)">
              {t === 0 ? "0" : t >= 10000 ? `${Math.round(t / 1000) / 10}万` : Math.round(t).toLocaleString("ja-JP")}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const v = values[i];
          const d = dayLabel(r.date);
          const x = padL + i * step + (step - barW) / 2;
          return (
            <g key={r.date}>
              <rect
                x={x}
                y={y(v)}
                width={barW}
                height={Math.max(0, padT + innerH - y(v))}
                rx={2}
                fill={hover === i ? "var(--color-warn)" : "var(--color-accent)"}
                tabIndex={0}
                aria-label={`${d.day}日（${d.weekday}） ${label} ${amt(v)} 円`}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                onClick={() => setHover(i)}
              />
              {(d.day === 1 || d.day % 5 === 0) && (
                <text x={x + barW / 2} y={H - 6} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
                  {d.day}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="text-sm num" style={{ color: "var(--color-muted)", minHeight: 22 }}>
        {shown ? `${dayLabel(shown.date).day}日（${dayLabel(shown.date).weekday}） ${label} ${amt(shown[valueKey])} 円` : `棒に触れると、その日の${label}が出ます。縦軸は円。`}
      </figcaption>
    </figure>
  );
}
