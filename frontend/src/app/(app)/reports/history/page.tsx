"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MonthPicker, RequireCaps } from "@/components/common";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { amt, cnt, ExportButton, fmtId, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtDate, fmtDateTime, fmtTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Money } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Move = {
  id: number; date: string; kind: string; kind_label: string; item_name: string; label: string; direction: "in" | "out"; amount: Money; reason: string;
  employee_name: string; occurred_at: string; is_voided: boolean; void_reason: string; in_profit: boolean; performed_by_name: string;
};
type CashHistory = {
  month: string; summary: { direction: "in" | "out"; label: string; is_item: boolean; count: number; amount: Money; in_profit: boolean }[]; rows: Move[];
  totals: { in: Money; out: Money; profit_in: Money; profit_out: Money }; profit_kind_note: string;
};
type Deletions = {
  cancelled: { id: number; public_id: string; slip_number: number; business_date: string; table_name: string; guest_count: number; customer: string; opened_at: string; closed_at: string | null; cancel_reason: string }[];
  logs: { id: number; at: string; action: string; action_label: string; user_name: string; visit_id: number | null; public_id: string; table_name: string; business_date: string; detail: string; amount: Money | null; reason: string }[];
  note: string;
};
type Tab = "cash" | "deletions";

export default function HistoryPage() {
  return (
    <RequireCaps anyCaps={["dashboard.view", "sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const { can } = useSession();
  const tabs: { key: Tab; label: string }[] = [
    ...(can("dashboard.view") ? [{ key: "cash" as Tab, label: "入出金の履歴" }] : []),
    ...(can("sales.view") ? [{ key: "deletions" as Tab, label: "伝票の削除履歴" }] : []),
  ];
  const [tab, setTab] = useState<Tab>(tabs[0]?.key ?? "cash");
  return (
    <div>
      <PageHeader title="履歴" subtitle="レジの入出金の履歴と、伝票・会計・明細を取り消した履歴です。ここからは変更できません。" />
      <ReportNav />
      <div className="flex flex-wrap gap-2 mb-3" role="tablist">
        {tabs.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={`btn btn-sm ${tab === t.key ? "btn-primary" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "cash" ? <CashHistoryTab /> : <DeletionsTab />}
    </div>
  );
}

function CashHistoryTab() {
  const bd = useLoad<{ business_date: string }>("/api/store/business-date");
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => {
    if (bd.data && month === null) setMonth(bd.data.business_date.slice(0, 7));
  }, [bd.data, month]);
  const q = useLoad<CashHistory>(month ? `/api/reports/cash-history?month=${month}` : null);
  const d = q.data;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {month && <MonthPicker value={month} onChange={setMonth} />}
        {month && <ExportButton path={`/api/reports/export/cash-history.xlsx?month=${month}`} cap="export.cash" />}
      </div>
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {(q.loading || !month) && <Spinner />}
      {d && (
        <>
          <ReportTable label="項目ごとの合計">
            <thead>
              <tr>
                <th>入金／出金</th>
                <th>項目</th>
                <th className="r">件数</th>
                <th className="r">金額</th>
                <th>粗利</th>
              </tr>
            </thead>
            <tbody>
              <tr className="total-row">
                <td>月の合計</td>
                <td>
                  入金 {amt(d.totals.in)} ／ 出金 {amt(d.totals.out)}
                </td>
                <td className="r">{cnt(d.summary.reduce((n, g) => n + g.count, 0))}</td>
                <td className="r">{amt(String(Number(d.totals.in) - Number(d.totals.out)))}</td>
                <td>
                  粗利に含める 入金 {amt(d.totals.profit_in)} ／ 出金 {amt(d.totals.profit_out)}
                </td>
              </tr>
              {d.summary.map((g) => (
                <tr key={`${g.direction}-${g.label}-${g.in_profit}`}>
                  <td>{g.direction === "in" ? "入金" : "出金"}</td>
                  <td className="font-semibold">
                    {g.label}
                    {g.is_item && <span className="badge ml-2">項目</span>}
                  </td>
                  <td className="r">{cnt(g.count)}</td>
                  <td className="r">{amt(g.amount)}</td>
                  <td>{g.in_profit ? "含める" : "含めない"}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>
          <h2 className="font-bold">明細</h2>
          <ReportTable label="入出金の明細">
            <thead>
              <tr>
                <th>営業日</th>
                <th>時刻</th>
                <th>入金／出金</th>
                <th>項目</th>
                <th className="r">金額</th>
                <th>内容</th>
                <th>従業員</th>
                <th>粗利</th>
                <th>登録した人</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r) => (
                <tr key={r.id} style={r.is_voided ? { opacity: 0.55 } : undefined}>
                  <td className="num">
                    <Link href={`/reports/daily/${r.date}`} className="underline">
                      {fmtDate(r.date)}
                    </Link>
                  </td>
                  <td className="num">{fmtTime(r.occurred_at)}</td>
                  <td>{r.direction === "in" ? "入金" : "出金"}</td>
                  <td>
                    {r.label}
                    {r.item_name && <span className="text-sm ml-1" style={{ color: "var(--color-muted)" }}>（{r.kind_label}）</span>}
                  </td>
                  <td className="r">{amt(r.amount)}</td>
                  <td>
                    {r.reason}
                    {r.is_voided && <span className="badge badge-danger ml-2">取消: {r.void_reason}</span>}
                  </td>
                  <td>{r.employee_name || "--"}</td>
                  <td>{r.in_profit ? "含める" : "含めない"}</td>
                  <td>{r.performed_by_name || "--"}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>
          {d.rows.length === 0 && <Empty>この月の入出金はありません。</Empty>}
          <ReportNote>取り消した入出金は明細に薄く出しますが、合計には入れていません。{d.profit_kind_note} 入出金項目は「設定 → 入出金項目・名称変更」で決めます。</ReportNote>
        </>
      )}
    </div>
  );
}

function DeletionsTab() {
  const [range, setRange, businessDate] = useDateRange();
  const qs = range ? `from=${range.from}&to=${range.to}` : null;
  const q = useLoad<Deletions>(qs ? `/api/reports/deletions?${qs}` : null);
  const d = q.data;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangeBar range={range} onChange={setRange} businessDate={businessDate} />
        <ExportButton path={qs ? `/api/reports/export/deletions.xlsx?${qs}` : null} cap="export.sales" />
      </div>
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {d && (
        <>
          <h2 className="font-bold">取消の操作（新しい順）</h2>
          <ReportTable label="取消の操作">
            <thead>
              <tr>
                <th>操作日時</th>
                <th>操作</th>
                <th>営業日</th>
                <th>伝票ID</th>
                <th>卓</th>
                <th>内容</th>
                <th className="r">金額</th>
                <th>理由</th>
                <th>操作した人</th>
              </tr>
            </thead>
            <tbody>
              {d.logs.map((r) => (
                <tr key={r.id}>
                  <td className="num">{fmtDateTime(r.at)}</td>
                  <td className="font-semibold">{r.action_label}</td>
                  <td className="num">{r.business_date ? fmtDate(r.business_date) : "--"}</td>
                  <td className="num">
                    {r.visit_id ? (
                      <Link href={`/reports/slips/${r.visit_id}`} className="underline">
                        {fmtId(r.public_id)}
                      </Link>
                    ) : (
                      "--"
                    )}
                  </td>
                  <td>{r.table_name || "--"}</td>
                  <td>{r.detail || "--"}</td>
                  <td className="r">{amt(r.amount)}</td>
                  <td>{r.reason || "--"}</td>
                  <td>{r.user_name || "--"}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>
          {d.logs.length === 0 && <Empty>この期間に取消の操作はありません。</Empty>}
          <h2 className="font-bold">取消済みの伝票</h2>
          <ReportTable label="取消済みの伝票">
            <thead>
              <tr>
                <th>営業日</th>
                <th>伝票ID</th>
                <th>卓</th>
                <th className="r">人数</th>
                <th>お客様</th>
                <th>入店</th>
                <th>取消</th>
                <th>理由</th>
              </tr>
            </thead>
            <tbody>
              {d.cancelled.map((r) => (
                <tr key={r.id}>
                  <td className="num">{fmtDate(r.business_date)}</td>
                  <td className="num">
                    <Link href={`/reports/slips/${r.id}`} className="underline">
                      {fmtId(r.public_id)}
                    </Link>
                  </td>
                  <td>{r.table_name}</td>
                  <td className="r">{cnt(r.guest_count)}</td>
                  <td>{r.customer || "--"}</td>
                  <td className="num">{fmtTime(r.opened_at)}</td>
                  <td className="num">{r.closed_at ? fmtTime(r.closed_at) : "--"}</td>
                  <td>{r.cancel_reason || "--"}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>
          {d.cancelled.length === 0 && <Empty>この期間に取消済みの伝票はありません。</Empty>}
          <ReportNote>{d.note}</ReportNote>
        </>
      )}
    </div>
  );
}
