"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MonthPicker, RequireCaps } from "@/components/common";
import { useBusinessDate } from "@/components/DateRange";
import { amt, dayLabel, ExportButton, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import type { CashMonth } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";

export default function CashMonthPage() {
  return (
    <RequireCaps caps={["dashboard.view"]}>
      <Inner />
    </RequireCaps>
  );
}

const signed = (v: string | null) => (v === null ? "--" : Number(v) < 0 ? `−${amt(String(-Number(v)))}` : amt(v));

function Inner() {
  const bd = useBusinessDate();
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => {
    if (bd && month === null) setMonth(bd.slice(0, 7));
  }, [bd, month]);
  const q = useLoad<CashMonth>(month ? `/api/reports/cash?month=${month}` : null);
  const d = q.data;
  return (
    <div>
      <PageHeader title="レジ精算（月間）" subtitle="開店した営業日ごとのレジの記録。数字は「開店・日締め・現金」と同じ計算です。" actions={
          month ? (
            <>
              <MonthPicker value={month} onChange={setMonth} />
              <ExportButton path={`/api/reports/export/cash.xlsx?month=${month}`} cap="export.cash" />
            </>
          ) : null
        }
      />
      <ReportNav />
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {(q.loading || !month) && <Spinner />}
      {d && (
        <>
          <ReportTable label="レジ精算（月間）">
            <thead>
              <tr>
                <th className="sticky-col">営業日</th>
                <th className="r">本日釣銭</th>
                <th className="r">現金売上</th>
                <th className="r">入金合計</th>
                <th className="r">出金合計</th>
                <th className="r">レジ内現金（理論値）</th>
                <th className="r">レジ内現金（実際）</th>
                <th className="r">現金過不足</th>
                <th className="r">預金金額</th>
                <th className="r">翌日釣銭準備金</th>
                <th>状態</th>
              </tr>
            </thead>
            <tbody>
              <tr className="total-row">
                <td className="sticky-col">合計 {d.rows.length}日</td>
                <td />
                <td className="r">{amt(d.totals.cash_sales)}</td>
                <td className="r">{amt(d.totals.cash_in)}</td>
                <td className="r">{amt(d.totals.cash_out)}</td>
                <td colSpan={2} />
                <td className="r">{signed(d.totals.difference)}</td>
                <td className="r">{amt(d.totals.set_aside_amount)}</td>
                <td colSpan={2} />
              </tr>
              {d.rows.map((r) => {
                const dl = dayLabel(r.date);
                return (
                  <tr key={r.date}>
                    <td className="sticky-col">
                      <Link href={`/reports/daily/${r.date}`} className="underline num">
                        {dl.day}日（{dl.weekday}）
                      </Link>
                    </td>
                    <td className="r">{amt(r.opening_float)}</td>
                    <td className="r">{amt(r.cash_sales)}</td>
                    <td className="r">{amt(r.cash_in)}</td>
                    <td className="r">{amt(r.cash_out)}</td>
                    <td className="r">{amt(r.theoretical_cash)}</td>
                    <td className="r">{r.counted_cash === null ? "--" : amt(r.counted_cash)}</td>
                    <td className="r" style={r.difference !== null && Number(r.difference) !== 0 ? { color: "var(--color-danger)" } : undefined}>
                      {signed(r.difference)}
                    </td>
                    <td className="r">{r.set_aside_amount === null ? "--" : amt(r.set_aside_amount)}</td>
                    <td className="r">{r.next_day_float === null ? "--" : amt(r.next_day_float)}</td>
                    <td>
                      <span className={`badge ${r.status === "open" ? "badge-accent" : ""}`}>{r.status_label}</span>
                      {r.difference_reason && <span className="text-sm ml-1">{r.difference_reason}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </ReportTable>
          {d.rows.length === 0 && <Empty>この月に開店した営業日はありません。</Empty>}
          <ReportNote>{d.formula_note} 実際の現金・過不足・預金金額・翌日釣銭準備金は、締めたときの記録です。</ReportNote>
        </>
      )}
    </div>
  );
}
