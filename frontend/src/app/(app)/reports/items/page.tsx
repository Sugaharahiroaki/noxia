"use client";

import { useState } from "react";
import { RequireCaps } from "@/components/common";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { amt, cnt, dayLabel, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { downloadFile, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Money } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type ProductRow = { product_id: number | null; name: string; category_name: string; category_kind: string; quantity: string; amount: Money; by_date: Record<string, string> };
type CastRow = { employee_id: number | null; employee_name: string; quantity: string; amount: Money; items: { product_id: number | null; name: string; quantity: string; amount: Money }[] };
type ItemReport = { from: string; to: string; dates: string[]; products: ProductRow[]; casts: CastRow[]; totals: { quantity: string; amount: Money; by_date: Record<string, string> }; note: string };
type Tab = "date" | "cast";

export default function ItemReportPage() {
  return (
    <RequireCaps caps={["sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const { can } = useSession();
  const toast = useToast();
  const [range, setRange, businessDate] = useDateRange();
  const [tab, setTab] = useState<Tab>("date");
  const qs = range ? `from=${range.from}&to=${range.to}` : null;
  const q = useLoad<ItemReport>(qs ? `/api/reports/items?${qs}` : null);
  const d = q.data;
  // キャスト × 商品の表の列（金額の大きい商品から）
  const names = d ? d.products.map((p) => p.name) : [];

  return (
    <div>
      <PageHeader
        title="商品集計"
        subtitle="会計が確定した商品の個数と金額。日付 × 商品と、キャスト × 商品。"
        actions={
          can("export.sales") && qs ? (
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/reports/export/items.csv?${qs}`).catch((e) => toast.show({ message: errorMessage(e), tone: "error" }))}>
              CSV 出力
            </button>
          ) : null
        }
      />
      <ReportNav />
      <div className="space-y-3">
        <DateRangeBar range={range} onChange={setRange} businessDate={businessDate} />
        <div className="flex flex-wrap gap-2" role="tablist">
          <button role="tab" aria-selected={tab === "date"} className={`btn btn-sm ${tab === "date" ? "btn-primary" : ""}`} onClick={() => setTab("date")}>
            日付 × 商品（個数）
          </button>
          <button role="tab" aria-selected={tab === "cast"} className={`btn btn-sm ${tab === "cast" ? "btn-primary" : ""}`} onClick={() => setTab("cast")}>
            キャスト × 商品（個数と金額）
          </button>
        </div>
        <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
        {q.loading && <Spinner />}
        {d && tab === "date" && (
          <>
            <ReportTable label="日付 × 商品">
              <thead>
                <tr>
                  <th className="sticky-col">商品</th>
                  <th>カテゴリー</th>
                  <th className="r">個数</th>
                  <th className="r">金額</th>
                  {d.dates.map((x) => {
                    const dl = dayLabel(x);
                    return (
                      <th key={x} className="r">
                        {Number(x.slice(5, 7))}/{dl.day}（{dl.weekday}）
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  <td className="sticky-col">合計 {d.products.length}品</td>
                  <td />
                  <td className="r">{cnt(d.totals.quantity)}</td>
                  <td className="r">{amt(d.totals.amount)}</td>
                  {d.dates.map((x) => (
                    <td key={x} className="r">
                      {cnt(d.totals.by_date[x])}
                    </td>
                  ))}
                </tr>
                {d.products.map((p) => (
                  <tr key={`${p.product_id}-${p.name}`}>
                    <td className="sticky-col font-semibold">{p.name}</td>
                    <td>{p.category_name || "--"}</td>
                    <td className="r">{cnt(p.quantity)}</td>
                    <td className="r">{amt(p.amount)}</td>
                    {d.dates.map((x) => (
                      <td key={x} className="r">
                        {cnt(p.by_date[x])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </ReportTable>
            {d.products.length === 0 && <Empty>この期間に会計が確定した商品はありません。</Empty>}
          </>
        )}
        {d && tab === "cast" && (
          <>
            <ReportTable label="キャスト × 商品">
              <thead>
                <tr className="group-head">
                  <th rowSpan={2} className="sticky-col">
                    キャスト
                  </th>
                  <th colSpan={2}>合計</th>
                  {names.map((n) => (
                    <th key={n} colSpan={2}>
                      {n}
                    </th>
                  ))}
                </tr>
                <tr>
                  <th className="r">個数</th>
                  <th className="r">金額</th>
                  {names.flatMap((n) => [
                    <th key={`${n}-q`} className="r">
                      個数
                    </th>,
                    <th key={`${n}-a`} className="r">
                      金額
                    </th>,
                  ])}
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  <td className="sticky-col">合計</td>
                  <td className="r">{cnt(d.totals.quantity)}</td>
                  <td className="r">{amt(d.totals.amount)}</td>
                  {d.products.flatMap((p) => [
                    <td key={`${p.name}-q`} className="r">
                      {cnt(p.quantity)}
                    </td>,
                    <td key={`${p.name}-a`} className="r">
                      {amt(p.amount)}
                    </td>,
                  ])}
                </tr>
                {d.casts.map((c) => {
                  const by = new Map(c.items.map((i) => [i.name, i]));
                  return (
                    <tr key={c.employee_id ?? "none"} style={c.employee_id === null ? { opacity: 0.7 } : undefined}>
                      <td className="sticky-col font-semibold">{c.employee_name}</td>
                      <td className="r">{cnt(c.quantity)}</td>
                      <td className="r font-semibold">{amt(c.amount)}</td>
                      {names.flatMap((n) => [
                        <td key={`${n}-q`} className="r">
                          {cnt(by.get(n)?.quantity)}
                        </td>,
                        <td key={`${n}-a`} className="r">
                          {amt(by.get(n)?.amount)}
                        </td>,
                      ])}
                    </tr>
                  );
                })}
              </tbody>
            </ReportTable>
            {d.casts.length === 0 && <Empty>この期間に会計が確定した商品はありません。</Empty>}
          </>
        )}
        {d && <ReportNote>{d.note}</ReportNote>}
      </div>
    </div>
  );
}
