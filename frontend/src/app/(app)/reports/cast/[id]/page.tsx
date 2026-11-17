"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { MonthPicker } from "@/components/common";
import { GroupToggles, PayCells, PayHead, useMonthReport } from "@/components/PeopleReport";
import { amt, dayLabel, ExportButton, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtTime } from "@/lib/format";
import { payCell, payGroups, type PayCol, type PayRow, type PersonReport } from "@/lib/payColumns";
import { useTx } from "@/lib/terms";
import { useSession } from "@/lib/session";

export default function PersonReportPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <Inner />
    </Suspense>
  );
}

/** 報酬支払明細（1 人・1 か月）と日別表。全員分を見られる人は誰の分でも、キャスト本人は自分の分だけ（サーバーで判定） */
function Inner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { can } = useSession();
  const tx = useTx();
  const q = useMonthReport<PersonReport>((m) => `/api/reports/people/${id}?month=${m}`, /^\d{4}-\d{2}$/.test(params.get("month") ?? "") ? params.get("month") : null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const d = q.data;
  const cast = d?.employee.role === "cast";
  const withholding = !!d?.settings.withholding_enabled;
  const all = payGroups({ cast: !!cast, withholding });
  const groups = payGroups({ cast: !!cast, withholding, hidden });
  const t = d?.totals;
  const line = (text: string, key: keyof PayRow, kind: PayCol["kind"] = "money") => ({ label: tx(text), value: t ? payCell(t, { key, label: text, kind }) : "--" });

  return (
    <div>
      <PageHeader
        title={d ? `報酬支払明細 ${d.employee.name}` : "報酬支払明細"}
        subtitle={d ? `${d.store_name} ・ ${d.from.replaceAll("-", "/")} 〜 ${d.to.replaceAll("-", "/")}` : undefined}
        actions={
          <>
            {q.month && <MonthPicker value={q.month} onChange={q.setMonth} />}
            {q.month && d && <ExportButton path={`/api/reports/people/${id}.pdf?month=${q.month}`} label="PDF 出力" />}
            {can("payroll.view_all") && (
              <Link href={cast === false ? "/reports/staff" : "/reports/cast"} className="btn btn-sm">
                ← 集計表
              </Link>
            )}
          </>
        }
      />
      {can("sales.view") || can("dashboard.view") || can("payroll.view_all") ? <ReportNav /> : null}
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {d && t && !q.loading && (
        <div className="space-y-4">
          <section className="card" aria-label="報酬支払明細書">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <Box title="勤務" rows={[line("労働日数", "days", "count"), line("労働時間", "minutes", "hours"), line("平均時給", "avg_hourly")]} />
              <Box
                title="支給対象"
                rows={[
                  line("時間報酬", "wage"),
                  ...(cast
                    ? [
                        line("呼び小計バック", "back_yobi_subtotal"),
                        line("同伴小計バック", "back_douhan_subtotal"),
                        line("本指名バック", "back_nomination"),
                        line("場内バック", "back_jounai"),
                        line("呼びバック", "back_yobi"),
                        line("同伴バック", "back_douhan"),
                        line("ドリンクバック", "back_drink"),
                        line("ボトルバック", "back_bottle"),
                        line("フードバック", "back_food"),
                      ]
                    : []),
                  line("その他バック", "back_other"),
                  line("ボーナス（調整・正）", "bonus"),
                  line("マイナス（調整・負）", "minus"),
                ]}
              />
              <Box
                title="控除・支払い"
                rows={[line("控除（厚生費など）", "deducted"), ...(withholding ? [line("源泉所得税", "withheld")] : []), line("日払い（渡した額）", "paid")]}
                footer={
                  <>
                    <Sum label="総支給額" value={amt(t.gross)} />
                    <Sum label="残り支給額" value={Number(t.balance) < 0 ? `−${amt(String(-Number(t.balance)))}` : amt(t.balance)} />
                  </>
                }
              />
            </div>
          </section>

          <section className="space-y-2">
            <h2 className="font-bold">日別</h2>
            <GroupToggles all={all} hidden={hidden} onChange={setHidden} />
            <ReportTable label="日別表">
              <PayHead groups={groups} first="営業日・タイムカード" />
              <tbody>
                <tr className="total-row">
                  <td className="sticky-col">月の合計</td>
                  <PayCells row={t} groups={groups} />
                </tr>
                {d.days.map((r) => {
                  const dl = dayLabel(r.date);
                  return (
                    <tr key={r.date}>
                      <td className="sticky-col">
                        <span className="num font-semibold">
                          {dl.day}日（{dl.weekday}）
                        </span>
                        <span className="text-sm ml-2 num" style={{ color: "var(--color-muted)" }}>
                          {r.shifts.map((s) => `${fmtTime(s.clock_in)}〜${s.clock_out ? fmtTime(s.clock_out) : "勤務中"}${s.hourly_wage ? ` @${amt(s.hourly_wage)}` : ""}`).join(" / ") || "勤怠なし"}
                        </span>
                      </td>
                      <PayCells row={r} groups={groups} />
                    </tr>
                  );
                })}
              </tbody>
            </ReportTable>
            {d.days.length === 0 && <Empty>この月の勤怠・バック・支払いはありません。</Empty>}
            <ReportNote>
              月の合計は日別の合計と一致します。金額は既存の計算結果（勤怠の修正・バックの確定や取消・日払いの記録）をそのまま集めたもので、ここでは変更できません。
              {withholding ? "源泉所得税は、日払いのお渡しのときに計算・記録した額です。" : ""}
            </ReportNote>
          </section>
        </div>
      )}
    </div>
  );
}

function Box({ title, rows, footer }: { title: string; rows: { label: string; value: string }[]; footer?: React.ReactNode }) {
  return (
    <div>
      <h3 className="font-bold mb-1">{title}</h3>
      <dl className="space-y-0.5">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between gap-3">
            <dt style={{ color: "var(--color-muted)" }}>{r.label}</dt>
            <dd className="num font-semibold">{r.value}</dd>
          </div>
        ))}
      </dl>
      {footer && (
        <div className="mt-2 pt-2 border-t space-y-0.5" style={{ borderColor: "var(--color-border)" }}>
          {footer}
        </div>
      )}
    </div>
  );
}

function Sum({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 items-baseline">
      <span className="font-bold">{label}</span>
      <span className="num text-2xl font-bold">{value}</span>
    </div>
  );
}
