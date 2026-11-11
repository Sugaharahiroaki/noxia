"use client";

import { useEffect, useState } from "react";
import { addDays, fmtDate, monthStart } from "@/lib/format";
import { useLoad } from "@/lib/useLoad";

/** いまの営業日（レジ締めするまでは24時を過ぎても同じ営業日）。サーバーが決める。 */
export function useBusinessDate(): string | null {
  const q = useLoad<{ business_date: string }>("/api/store/business-date");
  return q.data?.business_date ?? null;
}

export type DateRange = { from: string; to: string };

/** 期間の状態。初期値は「本日（営業日）」1日分。営業日がわかるまでは null。 */
export function useDateRange(): [DateRange | null, (r: DateRange) => void, string | null] {
  const bd = useBusinessDate();
  const [range, setRange] = useState<DateRange | null>(null);
  useEffect(() => {
    if (bd && range === null) setRange({ from: bd, to: bd });
  }, [bd, range]);
  return [range, setRange, bd];
}

/** 期間の選択バー: 本日（営業日）／過去7日／今月 ＋ 日付を指定 */
export function DateRangeBar({ range, onChange, businessDate, children }: { range: DateRange | null; onChange: (r: DateRange) => void; businessDate: string | null; children?: React.ReactNode }) {
  const [custom, setCustom] = useState(false);
  if (!range || !businessDate) return null;
  const presets: { label: string; r: DateRange }[] = [
    { label: "本日（営業日）", r: { from: businessDate, to: businessDate } },
    { label: "過去7日", r: { from: addDays(businessDate, -6), to: businessDate } },
    { label: "今月", r: { from: monthStart(businessDate), to: businessDate } },
  ];
  const active = presets.find((p) => p.r.from === range.from && p.r.to === range.to);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {presets.map((p) => (
        <button
          key={p.label}
          className={`btn ${active?.label === p.label && !custom ? "btn-primary" : ""}`}
          onClick={() => {
            setCustom(false);
            onChange(p.r);
          }}
        >
          {p.label}
        </button>
      ))}
      <button className={`btn ${custom || !active ? "btn-primary" : ""}`} onClick={() => setCustom((v) => !v)}>
        日付を指定
      </button>
      {(custom || !active) && (
        <>
          <input type="date" className="input" style={{ minHeight: 48, width: 180 }} value={range.from} onChange={(e) => e.target.value && onChange({ from: e.target.value, to: range.to < e.target.value ? e.target.value : range.to })} />
          <span>〜</span>
          <input type="date" className="input" style={{ minHeight: 48, width: 180 }} value={range.to} onChange={(e) => e.target.value && onChange({ from: range.from > e.target.value ? e.target.value : range.from, to: e.target.value })} />
        </>
      )}
      <span className="text-lg font-semibold ml-1">
        {range.from === range.to ? `${fmtDate(range.from)} の営業日` : `${fmtDate(range.from)} 〜 ${fmtDate(range.to)}`}
      </span>
      {children}
    </div>
  );
}
