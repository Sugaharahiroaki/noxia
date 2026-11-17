"use client";

import { Tx } from "@/lib/terms";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { RequireCaps } from "@/components/common";
import { amt, cnt, dayLabel, fmtId, pct, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { addDays, fmtDate, fmtTime } from "@/lib/format";
import type { DailyReport, IncomeDay } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";

export default function DailyReportPage() {
  return (
    <RequireCaps caps={["dashboard.view"]}>
      <Inner />
    </RequireCaps>
  );
}

type Line = { label: string; get: (d: IncomeDay) => string; avg?: keyof DailyReport["average"]; strong?: boolean };
const LINES: Line[] = [
  { label: "総売上", get: (d) => amt(d.sales), avg: "sales", strong: true },
  { label: "現金売上", get: (d) => amt(d.cash), avg: "cash" },
  { label: "カード売上", get: (d) => amt(d.card), avg: "card" },
  { label: "売掛", get: (d) => amt(d.receivable), avg: "receivable" },
  { label: "女子売上（指名のある伝票の本計）", get: (d) => amt(d.cast_sales), avg: "cast_sales" },
  { label: "女子の人件費（時給＋バック）", get: (d) => amt(d.labor_cast), avg: "labor_cast" },
  { label: "給率", get: (d) => pct(d.pay_ratio) },
  { label: "男子の人件費", get: (d) => amt(d.labor_staff) },
  { label: "呼び小計（指名の組数／回数）", get: (d) => `${amt(d.yobi_subtotal)}（${cnt(d.yobi_groups)}組／${cnt(d.nomination_count)}回）` },
  { label: "同伴小計", get: (d) => amt(d.douhan_subtotal) },
  { label: "客（組／人数）", get: (d) => `${cnt(d.groups)}組／${cnt(d.guests)}名` },
  { label: "客単価", get: (d) => amt(d.per_guest) },
  { label: "組単価", get: (d) => amt(d.per_group) },
  { label: "入金（粗利対象）", get: (d) => amt(d.income) },
  { label: "出金（粗利対象）", get: (d) => amt(d.expenses) },
  { label: "粗利", get: (d) => amt(d.gross_profit), avg: "gross_profit", strong: true },
];

function Inner() {
  const { date } = useParams<{ date: string }>();
  const router = useRouter();
  const q = useLoad<DailyReport>(`/api/reports/daily?date=${date}`);
  const d = q.data;
  const dl = dayLabel(date);

  return (
    <div>
      <PageHeader
        title={`営業日報 ${fmtDate(date)}（${dl.weekday}）`}
        subtitle="当日・当月の累計・1 営業日あたりの平均（伝票がある日で割る）。"
        actions={
          <>
            <button className="btn btn-sm" onClick={() => router.push(`/reports/daily/${addDays(date, -1)}`)} aria-label="前の日">
              ◀ 前の日
            </button>
            <input type="date" className="input" style={{ minHeight: 44, width: 170 }} value={date} onChange={(e) => e.target.value && router.push(`/reports/daily/${e.target.value}`)} aria-label="日付" />
            <button className="btn btn-sm" onClick={() => router.push(`/reports/daily/${addDays(date, 1)}`)} aria-label="次の日">
              次の日 ▶
            </button>
            <Link href="/reports/monthly" className="btn btn-sm">
              月次へ
            </Link>
          </>
        }
      />
      <ReportNav />
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {d && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <section>
              <h2 className="font-bold mb-1">営業日報</h2>
              <ReportTable label="営業日報">
                <thead>
                  <tr>
                    <th>項目</th>
                    <th className="r">当日</th>
                    <th className="r">累計（当月）</th>
                    <th className="r">平均（1 営業日）</th>
                  </tr>
                </thead>
                <tbody>
                  {LINES.map((l) => (
                    <tr key={l.label} className={l.strong ? "total-row" : undefined}>
                      <td>{l.label}</td>
                      <td className="r">{l.get(d.today)}</td>
                      <td className="r">{l.get(d.cumulative)}</td>
                      <td className="r">{l.avg ? amt(d.average[l.avg]) : "--"}</td>
                    </tr>
                  ))}
                </tbody>
              </ReportTable>
              <ReportNote>
                営業日数（当月・この日まで）: {d.active_days}日。{d.profit_kind_note}
              </ReportNote>
            </section>

            <section>
              <h2 className="font-bold mb-1">現金管理</h2>
              {!d.cash ? (
                <Empty>この営業日は開店していません（レジの記録がありません）。</Empty>
              ) : (
                <>
                  <ReportTable label="現金管理">
                    <thead>
                      <tr>
                        <th>摘要</th>
                        <th className="r">収入金額</th>
                        <th className="r">支払金額</th>
                      </tr>
                    </thead>
                    <tbody>
                      <Cash label="本日釣銭（開始時）" inn={d.cash.opening_float} />
                      <Cash label="現金売上" inn={d.cash.cash_sales} />
                      <Cash label="入金（その他入金・釣銭補充・未収の回収）" inn={d.cash.cash_in} />
                      <Cash label="出金（経費・日払い・その他・途中回収・返金）" out={d.cash.cash_out} />
                      <Cash label="　うち日払い" out={d.cash.daily_pay} />
                      <tr className="total-row">
                        <td>レジ内現金（理論値）</td>
                        <td className="r" colSpan={2}>
                          {amt(d.cash.theoretical_cash)}
                        </td>
                      </tr>
                      <Cash label="レジ内現金（実際に数えた額）" inn={d.cash.counted_cash} wide />
                      <Cash label="現金過不足" inn={d.cash.difference} wide />
                      <Cash label="預金金額（入金用の取り分け）" inn={d.cash.set_aside_amount} wide />
                      <Cash label="翌日釣銭準備金" inn={d.cash.next_day_float} wide />
                      <Cash label="過不足（当月累計）" inn={d.cash_month.difference} wide />
                      <Cash label="預金金額（当月累計）" inn={d.cash_month.set_aside} wide />
                    </tbody>
                  </ReportTable>
                  <ReportNote>
                    {d.cash.status === "open" ? "この営業日はまだ締めていません（実際の現金・過不足は締めのあとに出ます）。" : `締め済み。${d.cash.difference_reason ? `差額の理由: ${d.cash.difference_reason}。` : ""}`}
                    {d.cash_formula_note} 締め・修正は{" "}
                    <Link href="/closing" className="underline">
                      開店・日締め・現金
                    </Link>
                    で行います。
                  </ReportNote>
                </>
              )}
            </section>
          </div>

          <section>
            <h2 className="font-bold mb-1">入金／出金</h2>
            {d.movements.length === 0 ? (
              <Empty>この日の入出金はありません。</Empty>
            ) : (
              <ReportTable label="入金／出金">
                <thead>
                  <tr>
                    <th>時刻</th>
                    <th>入出金</th>
                    <th>項目</th>
                    <th className="r">金額</th>
                    <th>備考</th>
                    <th>粗利</th>
                  </tr>
                </thead>
                <tbody>
                  {d.movements.map((m) => (
                    <tr key={m.id} style={m.is_voided ? { opacity: 0.5 } : undefined}>
                      <td className="num">{fmtTime(m.occurred_at)}</td>
                      <td>{m.direction === "in" ? "入金" : "出金"}</td>
                      <td>
                        {m.item_name || m.kind_label}
                        {m.is_voided && <span className="badge badge-danger ml-1">取消</span>}
                      </td>
                      <td className="r">{amt(m.amount)}</td>
                      <td>
                        {m.reason}
                        {m.employee_name && `（${m.employee_name}）`}
                      </td>
                      <td>{m.in_profit ? "含める" : "含めない"}</td>
                    </tr>
                  ))}
                </tbody>
              </ReportTable>
            )}
          </section>

          <section>
            <h2 className="font-bold mb-1">伝票一覧（この営業日）</h2>
            <ReportTable label="伝票一覧（この営業日）">
              <thead>
                <tr>
                  <th>伝票ID</th>
                  <th>卓</th>
                  <th>入店</th>
                  <th>退店</th>
                  <th className="r">時間</th>
                  <th className="r">客数</th>
                  <th>
                    <Tx>タグ</Tx>
                  </th>
                  <th>
                    <Tx>本指名</Tx>
                  </th>
                  <th>
                    <Tx>場内</Tx>
                  </th>
                  <th>
                    <Tx>呼び</Tx>
                  </th>
                  <th>
                    <Tx>同伴</Tx>
                  </th>
                  <th className="r">小計</th>
                  <th className="r">現金</th>
                  <th className="r">カード</th>
                  <th className="r">売掛</th>
                  <th className="r">合計</th>
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  <td>合計 {d.slip_totals.count}件</td>
                  <td colSpan={4} />
                  <td className="r">{cnt(d.slip_totals.guest_count)}</td>
                  <td colSpan={5} />
                  <td className="r">{amt(d.slip_totals.subtotal)}</td>
                  <td className="r">{amt(d.slip_totals.cash)}</td>
                  <td className="r">{amt(d.slip_totals.card)}</td>
                  <td className="r">{amt(d.slip_totals.receivable)}</td>
                  <td className="r">{amt(d.slip_totals.total)}</td>
                </tr>
                {d.slips.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link href={`/reports/slips/${r.id}`} className="underline" style={{ fontFamily: "ui-monospace, Consolas, monospace" }}>
                        {fmtId(r.public_id)}
                      </Link>
                    </td>
                    <td>{r.table_name}</td>
                    <td className="num">{fmtTime(r.opened_at)}</td>
                    <td className="num">{r.closed_at ? fmtTime(r.closed_at) : "--"}</td>
                    <td className="r">{r.minutes === null ? "--" : `${r.minutes}分`}</td>
                    <td className="r">{cnt(r.guest_count)}</td>
                    <td>{r.tags.map((x) => x.name).join("・") || "--"}</td>
                    <td>{r.nomination.join("・") || "--"}</td>
                    <td>{r.jounai.join("・") || "--"}</td>
                    <td>{r.yobi.join("・") || "--"}</td>
                    <td>{r.douhan.join("・") || "--"}</td>
                    <td className="r">{amt(r.subtotal)}</td>
                    <td className="r">{amt(r.cash)}</td>
                    <td className="r">{amt(r.card)}</td>
                    <td className="r">{amt(r.receivable)}</td>
                    <td className="r font-semibold">{amt(r.total)}</td>
                  </tr>
                ))}
              </tbody>
            </ReportTable>
          </section>
        </div>
      )}
    </div>
  );
}

function Cash({ label, inn, out, wide }: { label: string; inn?: string | null; out?: string | null; wide?: boolean }) {
  if (wide)
    return (
      <tr>
        <td>{label}</td>
        <td className="r" colSpan={2}>
          {inn === null || inn === undefined ? "--" : Number(inn) < 0 ? `−${amt(String(-Number(inn)))}` : amt(inn)}
        </td>
      </tr>
    );
  return (
    <tr>
      <td>{label}</td>
      <td className="r">{inn !== undefined ? amt(inn) : ""}</td>
      <td className="r">{out !== undefined ? amt(out) : ""}</td>
    </tr>
  );
}
