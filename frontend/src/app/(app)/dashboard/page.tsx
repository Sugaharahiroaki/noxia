"use client";

import { useMemo, useState } from "react";
import { Empty, ErrorBox, PageHeader, SaveStatus, Spinner, Stat, type SaveState } from "@/components/ui";
import { downloadFile } from "@/lib/api";
import { addDays, fmtDate, fmtMinutes, monthStart, todayJst, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

type Summary = {
  total: string;
  checkout_count: number;
  visit_count: number;
  guest_count: number;
  avg_per_guest: string | null;
  cash: string;
  card: string;
  receivable: string;
  refunds: string;
};
type DailyRow = { date: string; total: string; checkouts: number; guests: number; avg_per_guest: string | null; cash: string; card: string; receivable: string };
type Daily = { from: string; to: string; rows: DailyRow[]; summary: Summary };
type MonthlyRow = Summary & { month: string };
type Breakdown = {
  from: string;
  to: string;
  summary: Summary;
  by_line_type: Record<string, { amount: string; count: number }>;
  by_course: { course: string | null; amount: string; count: number }[];
  by_product: { product_id: number | null; name: string; amount: string; quantity: string }[];
  by_cast: { employee_id: number | null; employee_name: string; attributed_sales: string; checkouts: number; nominations: number; douhan: number; back_total: string }[];
  labor: { worked_minutes: number; wage_estimate: string; back_total: string; labor_estimate: string; shifts_missing_wage: number; notice: string };
  purchases: { received_amount: string; cash_expenses: string; notice: string };
  cogs: { amount: string; lines_missing_cost: number };
  profit: { status: "estimate" | "estimate_partial" | "unavailable"; gross_profit: string | null; operating_estimate: string | null; notice: string };
};

const LINE_TYPE_LABELS: Record<string, string> = {
  course_set: "セット",
  extension: "延長",
  course_change: "コース変更",
  nomination: "指名",
  douhan: "同伴",
  product: "商品",
  discount: "値引き",
};

const EXPORTS: { kind: string; label: string; cap: string; reauth?: boolean }[] = [
  { kind: "sales", label: "売上（会計）", cap: "export.sales" },
  { kind: "payments", label: "支払い", cap: "export.sales" },
  { kind: "cash", label: "現金出納", cap: "export.cash" },
  { kind: "backs", label: "バック", cap: "export.backs" },
  { kind: "attendance", label: "勤怠", cap: "export.attendance" },
  { kind: "payroll", label: "従業員支払い", cap: "export.payroll", reauth: true },
  { kind: "inventory", label: "在庫変動", cap: "export.inventory" },
  { kind: "purchases", label: "発注", cap: "export.inventory" },
];

function Bar({ value, max, color }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="h-4 w-full rounded" style={{ background: "var(--color-panel3)" }} aria-hidden>
      <div className="h-4 rounded" style={{ width: `${pct}%`, background: color ?? "var(--color-accent)" }} />
    </div>
  );
}

export default function DashboardPage() {
  const { can, withReauth } = useSession();
  const today = todayJst();
  const [from, setFrom] = useState(monthStart(today));
  const [to, setTo] = useState(today);
  const q = useMemo(() => `from=${from}&to=${to}`, [from, to]);
  const daily = useLoad<Daily>(`/api/analytics/daily?${q}`);
  const breakdown = useLoad<Breakdown>(`/api/analytics/breakdown?${q}`);
  const monthly = useLoad<MonthlyRow[]>("/api/analytics/monthly?months=12");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });

  function preset(kind: "today" | "week" | "month" | "lastMonth") {
    if (kind === "today") {
      setFrom(today);
      setTo(today);
    } else if (kind === "week") {
      setFrom(addDays(today, -6));
      setTo(today);
    } else if (kind === "month") {
      setFrom(monthStart(today));
      setTo(today);
    } else {
      const lastEnd = addDays(monthStart(today), -1);
      setFrom(monthStart(lastEnd));
      setTo(lastEnd);
    }
  }

  async function exportCsv(kind: string, reauth?: boolean) {
    setSave({ kind: "saving", label: "CSV を作成中…" });
    try {
      const run = () => downloadFile(`/api/exports/${kind}.csv?${q}`);
      if (reauth) await withReauth(run);
      else await run();
      setSave({ kind: "saved", label: "ダウンロードしました" });
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  if (!can("dashboard.view")) return <Empty>この画面を利用する権限がありません。</Empty>;

  const s = daily.data?.summary;
  const b = breakdown.data;
  const maxDaily = Math.max(0, ...(daily.data?.rows.map((r) => toInt(r.total)) ?? [0]));
  const maxMonthly = Math.max(0, ...(monthly.data?.map((r) => toInt(r.total)) ?? [0]));

  return (
    <div className="space-y-4">
      <PageHeader
        title="経営ダッシュボード"
        subtitle="売上は確定した会計の請求額、現金・カード・未収は支払い記録から集計しています（営業日単位）。"
        actions={
          <>
            <button className="btn btn-sm" onClick={() => preset("today")}>
              今日
            </button>
            <button className="btn btn-sm" onClick={() => preset("week")}>
              過去7日
            </button>
            <button className="btn btn-sm" onClick={() => preset("month")}>
              今月
            </button>
            <button className="btn btn-sm" onClick={() => preset("lastMonth")}>
              先月
            </button>
            <input type="date" className="input w-auto" value={from} onChange={(e) => setFrom(e.target.value)} />
            <span>〜</span>
            <input type="date" className="input w-auto" value={to} onChange={(e) => setTo(e.target.value)} />
          </>
        }
      />
      <ErrorBox error={daily.error || breakdown.error} onRetry={() => { daily.reload(false); breakdown.reload(false); }} />
      {daily.loading && <Spinner />}

      {s && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="売上合計（税込）" value={yen(s.total)} accent="ok" />
          <Stat label="会計件数 / 組数" value={`${s.checkout_count} / ${s.visit_count}`} />
          <Stat label="客数" value={`${s.guest_count} 名`} />
          <Stat label="客単価" value={s.avg_per_guest ? yen(s.avg_per_guest) : "—"} />
          <Stat label="現金" value={yen(s.cash)} />
          <Stat label="カード" value={yen(s.card)} />
          <Stat label="未収（ツケ）" value={yen(s.receivable)} accent={toInt(s.receivable) > 0 ? "warn" : undefined} />
          <Stat label="返金" value={yen(s.refunds)} accent={toInt(s.refunds) > 0 ? "danger" : undefined} />
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <section className="card space-y-2">
          <h2 className="font-bold">日別売上（{fmtDate(from)} 〜 {fmtDate(to)}）</h2>
          {daily.data && daily.data.rows.length === 0 && <Empty>データがありません</Empty>}
          <div className="scroll-x max-h-[420px] overflow-y-auto">
            <table className="table num">
              <thead>
                <tr>
                  <th>営業日</th>
                  <th className="w-1/3">売上</th>
                  <th>金額</th>
                  <th>会計</th>
                  <th>客数</th>
                  <th>客単価</th>
                  <th>現金</th>
                  <th>カード</th>
                </tr>
              </thead>
              <tbody>
                {daily.data?.rows.map((r) => (
                  <tr key={r.date} style={{ opacity: toInt(r.total) === 0 ? 0.5 : 1 }}>
                    <td>{fmtDate(r.date)}</td>
                    <td>
                      <Bar value={toInt(r.total)} max={maxDaily} />
                    </td>
                    <td>{yen(r.total)}</td>
                    <td>{r.checkouts}</td>
                    <td>{r.guests}</td>
                    <td>{r.avg_per_guest ? yen(r.avg_per_guest) : "—"}</td>
                    <td>{yen(r.cash)}</td>
                    <td>{yen(r.card)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card space-y-2">
          <h2 className="font-bold">月別売上（直近12か月）</h2>
          {monthly.loading && <Spinner />}
          <ErrorBox error={monthly.error} onRetry={() => monthly.reload(false)} />
          <div className="scroll-x">
            <table className="table num">
              <thead>
                <tr>
                  <th>月</th>
                  <th className="w-1/3">売上</th>
                  <th>金額</th>
                  <th>客数</th>
                  <th>客単価</th>
                  <th>現金 / カード / 未収</th>
                </tr>
              </thead>
              <tbody>
                {monthly.data?.map((r) => (
                  <tr key={r.month} style={{ opacity: toInt(r.total) === 0 ? 0.5 : 1 }}>
                    <td>{r.month}</td>
                    <td>
                      <Bar value={toInt(r.total)} max={maxMonthly} color="var(--color-ok)" />
                    </td>
                    <td>{yen(r.total)}</td>
                    <td>{r.guest_count}</td>
                    <td>{r.avg_per_guest ? yen(r.avg_per_guest) : "—"}</td>
                    <td className="text-xs">
                      {yen(r.cash)} / {yen(r.card)} / {yen(r.receivable)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {breakdown.loading && <Spinner label="内訳を集計中…" />}
      {b && (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
            <section className="card space-y-2">
              <h2 className="font-bold">種別・コース別売上</h2>
              <table className="table num">
                <tbody>
                  {Object.entries(b.by_line_type).map(([k, v]) => (
                    <tr key={k}>
                      <td>{LINE_TYPE_LABELS[k] ?? k}</td>
                      <td className="text-right">{yen(v.amount)}</td>
                      <td className="text-right text-xs" style={{ color: "var(--color-muted)" }}>
                        {v.count}件
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {b.by_course.length > 0 && (
                <>
                  <h3 className="text-sm font-semibold mt-2" style={{ color: "var(--color-muted)" }}>
                    コース別
                  </h3>
                  <table className="table num">
                    <tbody>
                      {b.by_course.map((c, i) => (
                        <tr key={i}>
                          <td>{c.course ?? "（不明）"}</td>
                          <td className="text-right">{yen(c.amount)}</td>
                          <td className="text-right text-xs" style={{ color: "var(--color-muted)" }}>
                            {c.count}件
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </section>

            <section className="card space-y-2">
              <h2 className="font-bold">商品別売上（上位）</h2>
              {b.by_product.length === 0 && <Empty>商品の売上はありません</Empty>}
              <div className="max-h-[360px] overflow-y-auto">
                <table className="table num">
                  <tbody>
                    {b.by_product.map((p, i) => (
                      <tr key={i}>
                        <td>{p.name}</td>
                        <td className="text-right">{yen(p.amount)}</td>
                        <td className="text-right text-xs" style={{ color: "var(--color-muted)" }}>
                          {Number(p.quantity)}点
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="card space-y-2">
              <h2 className="font-bold">キャスト別売上・バック</h2>
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                「担当売上」は伝票の担当者に会計額を帰属させたもので、店舗全体の売上と重複しません。
              </p>
              {b.by_cast.length === 0 && <Empty>データがありません</Empty>}
              <div className="scroll-x">
                <table className="table num">
                  <thead>
                    <tr>
                      <th>従業員</th>
                      <th>担当売上</th>
                      <th>会計</th>
                      <th>指名</th>
                      <th>同伴</th>
                      <th>バック</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.by_cast.map((c, i) => (
                      <tr key={i}>
                        <td>{c.employee_name}</td>
                        <td>{yen(c.attributed_sales)}</td>
                        <td>{c.checkouts}</td>
                        <td>{c.nominations}</td>
                        <td>{c.douhan}</td>
                        <td>{yen(c.back_total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            <section className="card space-y-1">
              <h2 className="font-bold">勤務時間・人件費の目安</h2>
              <div className="num">勤務時間 {fmtMinutes(b.labor.worked_minutes)}</div>
              <div className="num">時給分 {yen(b.labor.wage_estimate)}</div>
              <div className="num">バック {yen(b.labor.back_total)}</div>
              <div className="num text-xl font-bold">合計の目安 {yen(b.labor.labor_estimate)}</div>
              {b.labor.shifts_missing_wage > 0 && (
                <div className="text-sm" style={{ color: "var(--color-warn)" }}>
                  時給未設定の勤怠が {b.labor.shifts_missing_wage} 件あります。
                </div>
              )}
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                {b.labor.notice}
              </p>
            </section>
            <section className="card space-y-1">
              <h2 className="font-bold">仕入・経費</h2>
              <div className="num">入荷金額（仕入額） {yen(b.purchases.received_amount)}</div>
              <div className="num">現金経費 {yen(b.purchases.cash_expenses)}</div>
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                {b.purchases.notice}
              </p>
            </section>
            <section className="card space-y-1">
              <h2 className="font-bold">売上原価</h2>
              <div className="num text-xl font-bold">{yen(b.cogs.amount)}</div>
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                販売数量 × 商品の原価設定。
                {b.cogs.lines_missing_cost > 0 && (
                  <span style={{ color: "var(--color-warn)" }}> 原価未設定の明細が {b.cogs.lines_missing_cost} 件あります。</span>
                )}
              </p>
            </section>
            <section className="card space-y-1" style={{ borderColor: b.profit.status === "unavailable" ? "var(--color-danger)" : b.profit.status === "estimate_partial" ? "var(--color-warn)" : "var(--color-border)" }}>
              <h2 className="font-bold">
                利益の目安
                <span className={`badge ml-2 ${b.profit.status === "unavailable" ? "badge-danger" : "badge-warn"}`}>
                  {b.profit.status === "unavailable" ? "算出不可" : b.profit.status === "estimate_partial" ? "概算（一部未設定）" : "概算"}
                </span>
              </h2>
              {b.profit.status === "unavailable" ? (
                <div className="text-lg">—</div>
              ) : (
                <>
                  <div className="num">粗利の目安 {yen(b.profit.gross_profit)}</div>
                  <div className="num text-xl font-bold">営業利益の目安 {yen(b.profit.operating_estimate)}</div>
                </>
              )}
              <p className="text-xs" style={{ color: "var(--color-muted)" }}>
                {b.profit.notice} 売上 − 売上原価 − 人件費の目安 − 現金経費。固定費は含みません。
              </p>
            </section>
          </div>
        </>
      )}

      <section className="card space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-bold">CSV 出力（{fmtDate(from)} 〜 {fmtDate(to)}）</h2>
          <SaveStatus state={save} />
        </div>
        <div className="flex flex-wrap gap-2">
          {EXPORTS.filter((e) => can(e.cap)).map((e) => (
            <button key={e.kind} className="btn btn-sm" onClick={() => exportCsv(e.kind, e.reauth)} disabled={save.kind === "saving"}>
              {e.label} CSV{e.reauth ? "（再認証）" : ""}
            </button>
          ))}
          {EXPORTS.every((e) => !can(e.cap)) && <span style={{ color: "var(--color-muted)" }}>CSV 出力の権限がありません。</span>}
        </div>
      </section>
    </div>
  );
}
