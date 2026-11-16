"use client";

import Link from "next/link";
import { useBusinessDate } from "@/components/DateRange";
import type { IncomeDay, MonthlyReport } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";
import { amt, cnt, pct } from "./ReportKit";
import { SalesTrendChart } from "./SalesTrendChart";
import { ErrorBox, Spinner } from "./ui";

/**
 * 経営ダッシュボードの「まとめ」: 今月と当日の 店舗状況・売上・経費・入金 と、売上の推移。
 * 数字は収支（月次）の API をそのまま使う（同じ集計を二重に持たない）。
 */
export function DashboardKpi() {
  const bd = useBusinessDate();
  const q = useLoad<MonthlyReport>(bd ? `/api/reports/monthly?month=${bd.slice(0, 7)}` : null);
  const d = q.data;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  if (!d || !bd) return <Spinner label="今月のまとめを読み込み中…" />;
  const today = d.rows.find((r) => r.date === bd) ?? null;
  return (
    <section className="card space-y-3" aria-label="今月のまとめ">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xl font-bold">今月のまとめ（{d.month.replace("-", "年")}月）</h2>
        <div className="flex gap-2">
          <Link href="/reports/monthly" className="btn btn-sm">
            収支（月次）
          </Link>
          <Link href={`/reports/daily/${bd}`} className="btn btn-sm">
            営業日報
          </Link>
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Block title="月間" r={d.totals} />
        {today ? <Block title="当日（いまの営業日）" r={today} /> : <div className="card-2">当日のデータがありません。</div>}
      </div>
      <div>
        <h3 className="font-bold mb-1">売上の推移（今月）</h3>
        <SalesTrendChart rows={d.rows} />
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        売上 = 確定した会計の請求額。女子本計 = 指名（本指名・場内）のある伝票の本計。人件費 = 時給×勤務時間＋確定したバック。粗利 = 売上 ＋ 入金 − 人件費 − 出金。{d.profit_kind_note}
      </p>
    </section>
  );
}

function Block({ title, r }: { title: string; r: IncomeDay }) {
  const row = (label: string, value: string, strong?: boolean) => (
    <div className="flex justify-between gap-3">
      <dt style={{ color: "var(--color-muted)" }}>{label}</dt>
      <dd className={`num ${strong ? "font-bold text-lg" : "font-semibold"}`}>{value}</dd>
    </div>
  );
  return (
    <div className="card-2">
      <h3 className="font-bold mb-2">{title}</h3>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        <dl className="space-y-0.5" aria-label={`${title} 店舗状況`}>
          {row("来店人数", `${cnt(r.guests)} 名`)}
          {row("来店組数", `${cnt(r.groups)} 組`)}
          {row("客単価", amt(r.per_guest))}
          {row("組単価", amt(r.per_group))}
          {row("女子本計", amt(r.cast_sales))}
        </dl>
        <dl className="space-y-0.5" aria-label={`${title} 売上`}>
          {row("売上 合計", amt(r.sales), true)}
          {row("現金", amt(r.cash))}
          {row("売掛", amt(r.receivable))}
          {row("カード", amt(r.card))}
        </dl>
        <dl className="space-y-0.5" aria-label={`${title} 経費`}>
          {row("経費 合計", amt(r.cost_total), true)}
          {row("人件費（女子）", amt(r.labor_cast))}
          {row("人件費（男子）", amt(r.labor_staff))}
          {row("諸経費（出金）", amt(r.expenses))}
        </dl>
        <dl className="space-y-0.5" aria-label={`${title} 入金・粗利`}>
          {row("入金 合計", amt(r.income))}
          {row("粗利", amt(r.gross_profit), true)}
          {row("給率", pct(r.pay_ratio))}
        </dl>
      </div>
    </div>
  );
}
