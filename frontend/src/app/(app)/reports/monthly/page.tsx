"use client";

import { Tx } from "@/lib/terms";
import Link from "next/link";
import { useEffect, useState } from "react";
import { MonthPicker, RequireCaps } from "@/components/common";
import { useBusinessDate } from "@/components/DateRange";
import { amt, cnt, dayLabel, ExportButton, pct, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { SalesTrendChart } from "@/components/SalesTrendChart";
import { ErrorBox, PageHeader, Spinner, Stat } from "@/components/ui";
import type { IncomeDay, MonthlyReport } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";

export default function MonthlyReportPage() {
  return (
    <RequireCaps caps={["dashboard.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const bd = useBusinessDate();
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => {
    if (bd && month === null) setMonth(bd.slice(0, 7));
  }, [bd, month]);
  const q = useLoad<MonthlyReport>(month ? `/api/reports/monthly?month=${month}` : null);
  const d = q.data;

  return (
    <div>
      <PageHeader title="収支（月次）" subtitle="1 行 = 1 営業日。売上は確定した会計、人件費は時給×勤務時間＋確定したバック（既存の計算結果）です。" actions={
          month ? (
            <>
              <MonthPicker value={month} onChange={setMonth} />
              <ExportButton path={`/api/reports/export/monthly.xlsx?month=${month}`} cap="export.cash" />
            </>
          ) : null
        }
      />
      <ReportNav />
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {(q.loading || !month) && <Spinner />}
      {d && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="売上" value={`${amt(d.totals.sales)} 円`} sub={`営業 ${d.active_days}日 ・ 1 日平均 ${amt(d.average.sales)}`} />
            <Stat label="入金（粗利対象）" value={`${amt(d.totals.income)} 円`} />
            <Stat label="経費（人件費＋出金）" value={`${amt(d.totals.cost_total)} 円`} sub={`人件費 ${amt(d.totals.labor_total)} ・ 出金 ${amt(d.totals.expenses)}`} />
            <Stat label="粗利" value={`${amt(d.totals.gross_profit)} 円`} accent={Number(d.totals.gross_profit) < 0 ? "danger" : "ok"} sub={`給率 ${pct(d.totals.pay_ratio)}`} />
          </div>
          <section className="card">
            <h2 className="font-bold mb-1">売上の推移</h2>
            <SalesTrendChart rows={d.rows} />
          </section>
          <ReportTable label="収支（月次）">
            <thead>
              <tr className="group-head">
                <th rowSpan={2} className="sticky-col">
                  日付
                </th>
                <th colSpan={4}>売上</th>
                <th colSpan={3}>女子売上</th>
                <th colSpan={2}>客</th>
                <th colSpan={8}>経費</th>
                <th rowSpan={2} className="r">
                  粗利
                </th>
              </tr>
              <tr>
                <th className="r">現金</th>
                <th className="r">売掛</th>
                <th className="r">カード</th>
                <th className="r">売上計</th>
                <th className="r">女子売上</th>
                <th className="r">
                  <Tx>呼び小計</Tx>
                </th>
                <th className="r">
                  <Tx>同伴小計</Tx>
                </th>
                <th className="r">人数</th>
                <th className="r">組数</th>
                <th className="r">人件費（女子）</th>
                <th className="r">給率</th>
                <th className="r">人件費（男子）</th>
                <th className="r">日払い（女子）</th>
                <th className="r">日払い（男子）</th>
                <th className="r">ボーナス</th>
                <th className="r">マイナス</th>
                <th className="r">入金／出金</th>
              </tr>
            </thead>
            <tbody>
              <Row r={d.totals} total />
              {d.rows.map((r) => (
                <Row key={r.date} r={r} />
              ))}
            </tbody>
          </ReportTable>
          <ReportNote>
            女子売上 = 指名（本指名・場内）のある伝票の本計。給率 = 人件費（女子）÷ 売上計。粗利 = 売上計 ＋ 入金 − 人件費 − 出金。{d.profit_kind_note}
            ボーナス・マイナスは手入力のバック調整（正・負）で、人件費に含めてあります。日払いは渡した額の表示で、粗利の計算には使いません（人件費として計上済みのため）。
          </ReportNote>
        </div>
      )}
    </div>
  );
}

function Row({ r, total }: { r: IncomeDay; total?: boolean }) {
  const dl = dayLabel(r.date);
  const io = Number(r.income) || Number(r.expenses) ? `${amt(r.income)} ／ ${amt(r.expenses)}` : "--";
  return (
    <tr className={total ? "total-row" : undefined} style={!total && !r.has_slips ? { opacity: 0.55 } : undefined}>
      <td className="sticky-col">
        {total ? (
          "合計"
        ) : (
          <Link href={`/reports/daily/${r.date}`} className="underline num" style={dl.weekend ? { color: "var(--color-warn)" } : undefined}>
            {dl.day}日（{dl.weekday}）
          </Link>
        )}
      </td>
      <td className="r">{amt(r.cash)}</td>
      <td className="r">{amt(r.receivable)}</td>
      <td className="r">{amt(r.card)}</td>
      <td className="r font-semibold">{amt(r.sales)}</td>
      <td className="r">{amt(r.cast_sales)}</td>
      <td className="r">{amt(r.yobi_subtotal)}</td>
      <td className="r">{amt(r.douhan_subtotal)}</td>
      <td className="r">{cnt(r.guests)}</td>
      <td className="r">{cnt(r.groups)}</td>
      <td className="r">{amt(r.labor_cast)}</td>
      <td className="r">{pct(r.pay_ratio)}</td>
      <td className="r">{amt(r.labor_staff)}</td>
      <td className="r">{amt(r.daily_pay_cast)}</td>
      <td className="r">{amt(r.daily_pay_staff)}</td>
      <td className="r">{amt(r.bonus)}</td>
      <td className="r">{amt(r.minus)}</td>
      <td className="r">{io}</td>
      <td className="r font-semibold" style={Number(r.gross_profit) < 0 ? { color: "var(--color-danger)" } : undefined}>
        {amt(r.gross_profit)}
      </td>
    </tr>
  );
}
