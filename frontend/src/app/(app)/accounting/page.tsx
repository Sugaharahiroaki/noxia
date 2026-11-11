"use client";

import Link from "next/link";
import { useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, downloadFile, newIdempotencyKey } from "@/lib/api";
import { ADJUSTMENT_KINDS, CATEGORY_TABS, MONTH_STATUS_BADGE, PAID_FROM, REMITTANCE_METHODS, todayJst } from "@/lib/accounting";
import { fmtDate, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AccountingOverview, Employee, WithholdingMonthView, WithholdingRecordBrief } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type MonthsResp = { category: string; year: number; months: WithholdingMonthView[] };

export default function WithholdingPage() {
  const { can } = useSession();
  const overview = useLoad<AccountingOverview>("/api/accounting/overview");
  const [year, setYear] = useState(new Date().getFullYear());
  const [category, setCategory] = useState<"hostess_fee" | "salary">("hostess_fee");
  const [month, setMonth] = useState<string | null>(null);
  const tp = overview.data?.taxpayer;
  const months = useLoad<MonthsResp>(tp ? `/api/accounting/withholding/months?year=${year}&category=${category}` : null, [tp?.id]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="源泉所得税管理"
        subtitle={
          <>
            法定の集計は事業者（源泉徴収義務者）単位、店舗別は管理用の内訳です。集計は実際の支払日の年月で行います。
            ホステス報酬の源泉所得税は納期の特例の対象外で、支払った月の翌月10日（休日なら休日明け）が納付期限です。
          </>
        }
      />
      <ErrorBox error={overview.error} onRetry={() => overview.reload(false)} />
      {overview.loading && <Spinner />}
      {overview.data && overview.data.stores.some((s) => !s.withholding_enabled) && (
        <div className="card-2" style={{ borderColor: "var(--color-warn)" }}>
          <span className="font-semibold">日払いで源泉所得税を差し引かない設定の店舗: </span>
          {overview.data.stores.filter((s) => !s.withholding_enabled).map((s) => s.name).join("、")}
          <span className="text-sm ml-2" style={{ color: "var(--color-muted)" }}>
            （その店舗のお渡しは源泉徴収の明細・集計に入りません。変更は「店舗設定」の「日払い（お渡し）」）
          </span>
        </div>
      )}
      {overview.data && !tp && (
        <div className="card space-y-2">
          <p className="text-lg font-semibold">この店舗の事業者（源泉徴収義務者）が設定されていません。</p>
          <p style={{ color: "var(--color-muted)" }}>法人または個人事業主の情報（所轄税務署・整理番号など）を登録し、店舗に設定すると集計できます。</p>
          <Link href="/accounting/settings" className="btn btn-primary w-fit">
            経理設定へ
          </Link>
        </div>
      )}
      {tp && (
        <>
          <div className="card-2 flex flex-wrap items-center gap-3">
            <span className="font-semibold text-lg">{tp.name}</span>
            <span className="badge">{tp.entity_type_label}</span>
            <span style={{ color: "var(--color-muted)" }}>
              所轄 {tp.tax_office_name || "未設定"} ・ 整理番号 {tp.reference_number || "未設定"} ・ 給与の納期の特例 {tp.payroll_due_special ? "あり" : "なし"}
            </span>
          </div>
          {(overview.data?.alerts.length ?? 0) > 0 && (
            <div className="space-y-2">
              {overview.data!.alerts.map((a) => (
                <div
                  key={`${a.category}-${a.period_start}`}
                  className="card-2 flex flex-wrap items-center gap-3"
                  style={{ borderColor: a.level === "overdue" ? "var(--color-danger)" : a.level === "soon" ? "var(--color-warn)" : "var(--color-border)" }}
                  role="alert"
                >
                  <span className={a.level === "overdue" ? "badge badge-danger" : a.level === "soon" ? "badge badge-warn" : "badge"}>
                    {a.level === "overdue" ? "期限超過" : a.level === "soon" ? "期限が近い" : "確認が必要"}
                  </span>
                  <span className="font-semibold">
                    {a.label} {a.category_label}
                  </span>
                  <span>納付期限 {fmtDate(a.due_date)}</span>
                  <span>未納付 {yen(a.unpaid_total)}</span>
                  <span className={MONTH_STATUS_BADGE[a.status] ?? "badge"}>{a.status_label}</span>
                  <button
                    className="btn btn-sm ml-auto"
                    onClick={() => {
                      setCategory(a.category as "hostess_fee" | "salary");
                      setYear(Number(a.period_start.slice(0, 4)));
                      setMonth(a.period_start.slice(0, 7));
                    }}
                  >
                    開く
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {CATEGORY_TABS.map((t) => (
              <button key={t.key} className={`btn ${category === t.key ? "btn-primary" : ""}`} onClick={() => setCategory(t.key)}>
                {t.label}
              </button>
            ))}
            <span className="ml-auto flex items-center gap-2">
              <button className="btn btn-sm" onClick={() => setYear((y) => y - 1)}>
                ◀
              </button>
              <span className="font-semibold num">{year}年</span>
              <button className="btn btn-sm" onClick={() => setYear((y) => y + 1)}>
                ▶
              </button>
            </span>
          </div>
          <ErrorBox error={months.error} onRetry={() => months.reload(false)} />
          {months.data && (
            <div className="card scroll-x">
              <table className="table">
                <thead>
                  <tr>
                    <th>対象年月</th>
                    <th>事業者</th>
                    <th className="text-right">報酬支払総額</th>
                    <th className="text-right">源泉徴収対象額</th>
                    <th className="text-right">源泉徴収済額</th>
                    <th className="text-right">対象人数</th>
                    <th className="text-right">支払件数</th>
                    <th className="text-right">未確定件数</th>
                    <th>納付期限</th>
                    <th>納付状況</th>
                  </tr>
                </thead>
                <tbody>
                  {months.data.months.map((m) => (
                    <tr
                      key={m.period_start}
                      className="cursor-pointer"
                      onClick={() => setMonth(m.period_start.slice(0, 7))}
                      style={month === m.period_start.slice(0, 7) ? { background: "var(--color-panel3)" } : undefined}
                    >
                      <td className="font-semibold">{m.label}</td>
                      <td>{m.taxpayer_name}</td>
                      <td className="text-right num">{yen(m.totals.gross_total)}</td>
                      <td className="text-right num">{m.totals.target_total !== undefined ? yen(m.totals.target_total) : "—"}</td>
                      <td className="text-right num">
                        {yen(m.totals.tax_total)}
                        {toInt(m.totals.adjustment_total) !== 0 && (
                          <span className="block text-xs" style={{ color: "var(--color-muted)" }}>
                            うち訂正 {yen(m.totals.adjustment_total, { sign: true })}
                          </span>
                        )}
                      </td>
                      <td className="text-right num">{m.totals.people}</td>
                      <td className="text-right num">{m.totals.payment_count}</td>
                      <td className="text-right num" style={m.totals.needs_review_count ? { color: "var(--color-warn)", fontWeight: 700 } : undefined}>
                        {m.totals.needs_review_count}
                      </td>
                      <td className="num">{fmtDate(m.due_date)}</td>
                      <td>
                        <span className={MONTH_STATUS_BADGE[m.status] ?? "badge"}>{m.status_label}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {month && <MonthDetail key={`${category}-${month}`} month={month} category={category} canDecide={can("tax.decide")} onChanged={() => months.reload()} />}
        </>
      )}
    </div>
  );
}

function MonthDetail({ month, category, canDecide, onChanged }: { month: string; category: string; canDecide: boolean; onChanged: () => Promise<void> | void }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<WithholdingMonthView>(`/api/accounting/withholding/month?month=${month}&category=${category}`);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [resolveTarget, setResolveTarget] = useState<WithholdingRecordBrief | null>(null);
  const [remitOpen, setRemitOpen] = useState(false);
  const [adjOpen, setAdjOpen] = useState(false);
  const [salaryOpen, setSalaryOpen] = useState(false);
  const canAggregate = can("tax.decide") || can("accounting.bookkeep");

  async function act(action: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    setErr(null);
    try {
      const run = () => api<WithholdingMonthView>(`/api/accounting/withholding/month/${action}`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { month, category, ...body } });
      const v = action === "confirm" ? await withReauth(run) : await run();
      q.setData({ ...(q.data as WithholdingMonthView), ...v });
      await Promise.all([q.reload(), onChanged()]);
      toast.show({ message: `${v.label}: ${v.status_label}` });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  if (q.loading) return <Spinner />;
  const v = q.data;
  if (!v) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const t = v.totals;
  const tag = `month=${month}&category=${category}`;

  return (
    <div className="card space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-bold">
          {v.label}（{v.category_label}）
        </h2>
        <span className={MONTH_STATUS_BADGE[v.status] ?? "badge"} style={{ fontSize: "1rem" }}>
          {v.status_label}
        </span>
        <span className="num">
          納付期限 {fmtDate(v.due_date)}
          {v.status !== "paid" && <span style={{ color: v.days_to_due < 0 ? "var(--color-danger)" : "var(--color-muted)" }}>（{v.days_to_due < 0 ? `${-v.days_to_due}日超過` : `あと${v.days_to_due}日`}）</span>}
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          {canAggregate && (
            <button className="btn" disabled={busy} onClick={() => act("aggregate")}>
              集計する
            </button>
          )}
          {canDecide && (
            <button
              className="btn btn-primary"
              disabled={busy || !v.month_id || ["owner_confirmed", "awaiting_payment", "paid"].includes(v.status)}
              onClick={() => setConfirmOpen(true)}
            >
              経営者確認
            </button>
          )}
          {canDecide && (v.status === "owner_confirmed" || v.status === "awaiting_payment") && (
            <button className="btn" disabled={busy} onClick={() => act("awaiting")}>
              納付待ちにする
            </button>
          )}
        </div>
      </div>
      <ErrorBox error={err || q.error} />
      {v.status === "corrected" && (
        <div className="card-2" style={{ borderColor: "var(--color-danger)" }} role="alert">
          <p className="font-semibold">確認した後に支払い・取消・訂正がありました。もう一度「集計する」→「経営者確認」をしてください。</p>
          <ul className="text-sm mt-1">
            {Object.entries(v.snapshot_diff).map(([k, d]) => (
              <li key={k} className="num">
                {k}: {d.before ?? "—"} → {d.after}
              </li>
            ))}
          </ul>
        </div>
      )}
      {v.confirmed_at && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          経営者確認: {v.confirmed_by}（{fmtDate(v.confirmed_at.slice(0, 10))}）
        </p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="報酬支払総額" value={yen(t.gross_total)} sub={category === "hostess_fee" ? "要確認の支払いを含む" : "給与計算ソフトの入力分"} />
        {t.target_total !== undefined && <Stat label="源泉徴収対象額（支払額）" value={yen(t.target_total)} />}
        {t.deduction_total !== undefined && <Stat label="支払額からの控除額" value={yen(t.deduction_total)} sub="5,000円×計算期間の日数（使った分）" />}
        <Stat label="納付する税額" value={yen(t.tax_total)} sub={`源泉徴収済 ${yen(t.withheld_total)} ・ 訂正 ${yen(t.adjustment_total, { sign: true })}`} accent="warn" />
        <Stat label="対象人数" value={`${t.people}人`} />
        <Stat label="支払件数" value={`${t.payment_count}件`} />
        <Stat label="未確定（要確認）" value={`${t.needs_review_count}件`} accent={t.needs_review_count ? "danger" : undefined} />
        <Stat label="納付済み" value={yen(v.remitted_total)} sub={`未納付 ${yen(v.unpaid_total)}`} accent={toInt(v.unpaid_total) === 0 && toInt(t.tax_total) > 0 ? "ok" : undefined} />
      </div>

      {(v.needs_review?.length ?? 0) > 0 && (
        <section className="space-y-2">
          <h3 className="font-bold text-lg">要確認の支払い（集計に含めていません）</h3>
          <div className="scroll-x">
            <table className="table">
              <thead>
                <tr>
                  <th>支払日</th>
                  <th>店舗</th>
                  <th>キャスト</th>
                  <th className="text-right">支払った報酬</th>
                  <th>理由</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {v.needs_review!.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{fmtDate(r.paid_on)}</td>
                    <td>{r.store_name}</td>
                    <td className="font-semibold">{r.employee_name}</td>
                    <td className="text-right num">{yen(r.gross_amount)}</td>
                    <td className="text-sm">{r.review_reasons.join(" ")}</td>
                    <td>
                      {canDecide && (
                        <button className="btn btn-sm" onClick={() => setResolveTarget(r)}>
                          確認して確定
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {v.by_store && v.by_store.length > 0 && (
        <section>
          <h3 className="font-bold text-lg mb-2">店舗別（管理用の内訳）</h3>
          <div className="scroll-x">
            <table className="table">
              <thead>
                <tr>
                  <th>店舗</th>
                  <th className="text-right">人数</th>
                  <th className="text-right">件数</th>
                  <th className="text-right">報酬総額</th>
                  <th className="text-right">対象額</th>
                  <th className="text-right">控除額</th>
                  <th className="text-right">源泉所得税</th>
                </tr>
              </thead>
              <tbody>
                {v.by_store.map((s) => (
                  <tr key={s.store_id}>
                    <td>{s.store_name}</td>
                    <td className="text-right num">{s.people}</td>
                    <td className="text-right num">{s.count}</td>
                    <td className="text-right num">{yen(s.gross)}</td>
                    <td className="text-right num">{yen(s.target)}</td>
                    <td className="text-right num">{yen(s.deduction)}</td>
                    <td className="text-right num">{yen(s.withholding)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {v.by_employee && v.by_employee.length > 0 && (
        <section>
          <h3 className="font-bold text-lg mb-2">キャスト別</h3>
          <div className="scroll-x">
            <table className="table">
              <thead>
                <tr>
                  <th>キャスト</th>
                  <th>店舗</th>
                  <th className="text-right">件数</th>
                  <th className="text-right">報酬総額</th>
                  <th className="text-right">対象額</th>
                  <th className="text-right">控除額</th>
                  <th className="text-right">源泉所得税</th>
                </tr>
              </thead>
              <tbody>
                {v.by_employee.map((e) => (
                  <tr key={e.employee_id}>
                    <td className="font-semibold">{e.employee_name}</td>
                    <td className="text-sm">{e.stores.join("、")}</td>
                    <td className="text-right num">{e.count}</td>
                    <td className="text-right num">{yen(e.gross)}</td>
                    <td className="text-right num">{yen(e.target)}</td>
                    <td className="text-right num">{yen(e.deduction)}</td>
                    <td className="text-right num">{yen(e.withholding)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {v.rows && (
        <section className="space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="font-bold text-lg">給与の源泉徴収（給与計算ソフトで計算した結果）</h3>
            {(can("tax.decide") || can("accounting.bookkeep")) && (
              <button className="btn btn-sm ml-auto" onClick={() => setSalaryOpen(true)}>
                ＋ 入力する
              </button>
            )}
          </div>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            NOXIA は給与所得の源泉徴収税額表（甲欄・乙欄・丙欄）による計算をしません。扶養控除等申告書・社会保険料などを反映した給与計算ソフトの結果を入力してください。
            {toInt(t.noxia_salary_count) > 0 && ` NOXIA での給与の支払い: ${t.noxia_salary_count}件 ${yen(t.noxia_salary_paid)}（照合用）`}
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>区分</th>
                <th className="text-right">人員</th>
                <th className="text-right">支給額</th>
                <th className="text-right">税額</th>
              </tr>
            </thead>
            <tbody>
              {v.rows.map((r) => (
                <tr key={r.kind}>
                  <td>{r.label}</td>
                  <td className="text-right num">{r.people}</td>
                  <td className="text-right num">{yen(r.gross)}</td>
                  <td className="text-right num">{yen(r.withholding)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(v.entries?.length ?? 0) > 0 && (
            <ul className="text-sm space-y-1">
              {v.entries!.map((e) => (
                <li key={e.id} style={{ opacity: e.is_voided ? 0.5 : 1 }}>
                  {fmtDate(e.paid_on)} {e.kind_label} {e.employee_name ?? "（合計入力）"} 支給 {yen(e.gross_amount)} 税額 {yen(e.withholding_amount)} {e.note}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {v.form && (
        <section className="space-y-2">
          <h3 className="font-bold text-lg">{v.form.form}（転記用）</h3>
          <p className="text-sm" style={{ color: "var(--color-warn)" }}>
            {v.form.notice}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <table className="table">
              <tbody>
                <tr>
                  <th>年度</th>
                  <td className="num">
                    {v.form.nendo}{" "}
                    <span className="text-xs" style={{ color: "var(--color-muted)" }}>
                      {v.form.nendo_note}
                    </span>
                  </td>
                </tr>
                <tr>
                  <th>税務署名</th>
                  <td>{v.form.taxpayer.tax_office_name || "（未設定）"}</td>
                </tr>
                <tr>
                  <th>整理番号</th>
                  <td className="num">{v.form.taxpayer.reference_number || "（未設定）"}</td>
                </tr>
                <tr>
                  <th>納期等の区分</th>
                  <td className="num">
                    {v.form.nouki.code}（{v.form.nouki.label}）
                  </td>
                </tr>
                <tr>
                  <th>納期限</th>
                  <td className="num">{fmtDate(v.form.due_date)}</td>
                </tr>
              </tbody>
            </table>
            <table className="table">
              <thead>
                <tr>
                  <th>区分</th>
                  <th className="text-right">人員</th>
                  <th className="text-right">支払額</th>
                  <th className="text-right">税額</th>
                </tr>
              </thead>
              <tbody>
                {v.form.rows.map((r) => (
                  <tr key={r.label}>
                    <td>
                      {r.code && <span className="badge mr-1">{r.code}</span>}
                      {r.label}
                    </td>
                    <td className="text-right num">
                      {r.people}
                      {r.people_note && <span className="text-xs">（{r.people_note}）</span>}
                    </td>
                    <td className="text-right num">{yen(r.payment_amount)}</td>
                    <td className="text-right num">{yen(r.tax)}</td>
                  </tr>
                ))}
                <tr>
                  <th>合計額</th>
                  <td></td>
                  <td></td>
                  <td className="text-right num font-bold">{yen(v.form.total)}</td>
                </tr>
                {v.form.tekiyo.map((x) => (
                  <tr key={x.label}>
                    <td colSpan={3}>摘要: {x.label}</td>
                    <td className="text-right num">{yen(x.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {v.form.warnings.map((w) => (
            <p key={w} className="text-sm" style={{ color: "var(--color-warn)" }}>
              ⚠ {w}
            </p>
          ))}
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/withholding/form.pdf?${tag}`).catch(setErr)}>
              転記用PDF
            </button>
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/withholding/summary.pdf?${tag}`).catch(setErr)}>
              月次集計PDF
            </button>
            {category === "hostess_fee" && (
              <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/withholding/detail.csv?${tag}`).catch(setErr)}>
                キャスト別明細CSV
              </button>
            )}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="font-bold text-lg">納付（経営者ご本人が e-Tax 等で納付）</h3>
          {canDecide && (
            <button className="btn btn-sm btn-primary ml-auto" disabled={!["owner_confirmed", "awaiting_payment", "paid", "overdue"].includes(v.status) || !v.confirmed_at} onClick={() => setRemitOpen(true)}>
              納付を記録する
            </button>
          )}
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          NOXIA は e-Tax の利用者識別番号・暗証番号などの認証情報を預からず、納付の代行もしません。下のリンクから e-Tax で「所得税徴収高計算書データ」を送信して納付し、納付後にここへ記録して証憑を保存してください。
        </p>
        <div className="flex flex-wrap gap-2">
          {v.etax_links?.map((l) => (
            <a key={l.url} className="btn btn-sm" href={l.url} target="_blank" rel="noopener noreferrer">
              {l.label} ↗
            </a>
          ))}
        </div>
        {(v.remittances?.length ?? 0) > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>納付日</th>
                <th>方法</th>
                <th className="text-right">金額</th>
                <th>メモ</th>
              </tr>
            </thead>
            <tbody>
              {v.remittances!.map((r) => (
                <tr key={r.id} style={{ opacity: r.is_voided ? 0.5 : 1 }}>
                  <td className="num">{fmtDate(r.paid_on)}</td>
                  <td>{r.method_label}</td>
                  <td className="text-right num">{yen(r.amount)}</td>
                  <td>
                    {r.note}
                    {r.is_voided && "（取消）"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="font-bold text-lg">訂正</h3>
          {canDecide && (
            <button className="btn btn-sm ml-auto" onClick={() => setAdjOpen(true)}>
              ＋ 訂正を登録
            </button>
          )}
        </div>
        {(v.adjustments?.length ?? 0) === 0 ? (
          <Empty>訂正はありません</Empty>
        ) : (
          <ul className="space-y-1">
            {v.adjustments!.map((a) => (
              <li key={a.id} className="card-2 flex flex-wrap gap-3" style={{ opacity: a.is_voided ? 0.5 : 1 }}>
                <span className="num">{fmtDate(a.applies_on)}</span>
                <span>{a.kind_label}</span>
                <span className="num font-semibold">{yen(a.amount_delta, { sign: true })}</span>
                <span className="text-sm">{a.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <AdvisorNote v={v} month={month} category={category} onSaved={() => q.reload()} />

      {confirmOpen && (
        <ConfirmDialog
          title={`${v.label} の源泉所得税を確認しました`}
          message={`納付する税額 ${yen(t.tax_total)}（対象 ${t.people}人・${t.payment_count}件）。この数字で帳票を作り、e-Tax で納付します。`}
          confirmLabel="確認した"
          busy={busy}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={async () => {
            await act("confirm");
            setConfirmOpen(false);
          }}
        />
      )}
      {resolveTarget && <ResolveModal record={resolveTarget} onClose={() => setResolveTarget(null)} onDone={async () => { setResolveTarget(null); await q.reload(); onChanged(); }} />}
      {remitOpen && <RemitModal v={v} month={month} category={category} onClose={() => setRemitOpen(false)} onDone={async () => { setRemitOpen(false); await q.reload(); onChanged(); }} />}
      {adjOpen && <AdjustmentModal month={month} category={category} onClose={() => setAdjOpen(false)} onDone={async () => { setAdjOpen(false); await q.reload(); onChanged(); }} />}
      {salaryOpen && <SalaryEntryModal month={month} onClose={() => setSalaryOpen(false)} onDone={async () => { setSalaryOpen(false); await q.reload(); onChanged(); }} />}
    </div>
  );
}

function AdvisorNote({ v, month, category, onSaved }: { v: WithholdingMonthView; month: string; category: string; onSaved: () => void }) {
  const { can } = useSession();
  const [note, setNote] = useState(v.advisor_note);
  const [err, setErr] = useState<unknown>(null);
  if (!can("tax.advise") && !v.advisor_note) return null;
  return (
    <section className="space-y-2">
      <h3 className="font-bold text-lg">税理士等の確認コメント</h3>
      {can("tax.advise") && !can("tax.decide") ? (
        <>
          <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          <ErrorBox error={err} />
          <button
            className="btn btn-sm"
            disabled={!v.month_id}
            onClick={async () => {
              try {
                await api("/api/accounting/withholding/month/advice", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { month, category, note } });
                onSaved();
              } catch (e) {
                setErr(e);
              }
            }}
          >
            コメントを保存
          </button>
        </>
      ) : (
        <p className="card-2 whitespace-pre-wrap">{v.advisor_note || "—"}</p>
      )}
    </section>
  );
}

function ResolveModal({ record, onClose, onDone }: { record: WithholdingRecordBrief; onClose: () => void; onDone: () => void }) {
  const { withReauth } = useSession();
  const [category, setCategory] = useState("hostess_fee");
  const [start, setStart] = useState(record.period_start ?? record.work_date ?? record.paid_on);
  const [end, setEnd] = useState(record.period_end ?? record.work_date ?? record.paid_on);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`${record.employee_name}さん ${fmtDate(record.paid_on)} の支払いを確定`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !note.trim()}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await withReauth(() =>
                  api(`/api/accounting/withholding/records/${record.id}/resolve`, { method: "POST", body: { category, period_start: start, period_end: end, note } }),
                );
                onDone();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            確定する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p>支払った報酬 {yen(record.gross_amount)}。手渡し済みの額は変わりません。ホステス報酬として確定すると、支払時に差し引かなかった税額を「支払時に未徴収」として支払月に計上します（本人からの回収・支払者負担の扱いは税理士等に確認してください）。</p>
        <div className="grid grid-cols-2 gap-2">
          {[
            { key: "hostess_fee", label: "ホステス等の報酬・料金" },
            { key: "salary", label: "給与所得（給与計算ソフトで計算）" },
          ].map((c) => (
            <button key={c.key} className={`btn ${category === c.key ? "btn-primary" : ""}`} onClick={() => setCategory(c.key)}>
              {c.label}
            </button>
          ))}
        </div>
        {category === "hostess_fee" && (
          <div className="grid grid-cols-2 gap-2">
            <Field label="報酬計算期間（開始）">
              <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label="報酬計算期間（終了）">
              <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </Field>
          </div>
        )}
        <Field label="確認した内容（必須）">
          <textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

function RemitModal({ v, month, category, onClose, onDone }: { v: WithholdingMonthView; month: string; category: string; onClose: () => void; onDone: () => void }) {
  const { withReauth } = useSession();
  const [paidOn, setPaidOn] = useState(todayJst());
  const [amount, setAmount] = useState(String(toInt(v.unpaid_total)));
  const [method, setMethod] = useState("etax_direct");
  const [paidFrom, setPaidFrom] = useState("bank");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [result, setResult] = useState<{ matched: boolean; difference: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`${v.label} の納付を記録`}
      onClose={busy ? undefined : onClose}
      footer={
        result ? (
          <button className="btn btn-primary" onClick={onDone}>
            閉じる
          </button>
        ) : (
          <>
            <button className="btn" onClick={onClose} disabled={busy}>
              やめる
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || toInt(amount) <= 0}
              onClick={async () => {
                setBusy(true);
                setErr(null);
                try {
                  const r = await withReauth(() =>
                    api<{ check: { matched: boolean; difference: string } }>("/api/accounting/withholding/remittances", {
                      method: "POST",
                      idempotencyKey: newIdempotencyKey(),
                      body: { month, category, paid_on: paidOn, amount, method, paid_from: paidFrom, note },
                    }),
                  );
                  setResult(r.check);
                } catch (e) {
                  setErr(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              記録する
            </button>
          </>
        )
      }
    >
      {result ? (
        <p className="text-lg font-semibold" style={{ color: result.matched ? "var(--color-ok)" : "var(--color-warn)" }}>
          {result.matched ? "✓ 納付すべき税額と一致しました（納付済み）。" : `納付すべき税額との差額 ${yen(result.difference, { sign: true })}。残りの納付、または訂正を確認してください。`}
        </p>
      ) : (
        <div className="space-y-3">
          <p className="num">納付する税額 {yen(v.totals.tax_total)} ・ 納付済み {yen(v.remitted_total)} ・ 未納付 {yen(v.unpaid_total)}</p>
          <div className="grid grid-cols-2 gap-2">
            <Field label="納付日">
              <input className="input" type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
            </Field>
            <Field label="金額">
              <input className="input num" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))} />
            </Field>
          </div>
          <Field label="納付方法">
            <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
              {REMITTANCE_METHODS.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="納付に使ったお金" hint="レジ現金を選ぶと、この店舗のレジから出金として記録します（現金残高と合わせるため）。">
            <select className="input" value={paidFrom} onChange={(e) => setPaidFrom(e.target.value)}>
              {PAID_FROM.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="メモ（受付番号など）">
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            納付の証憑（受信通知・領収証書）は「月次損益・経費・証憑」の証憑に「納付証憑」として保存してください。
          </p>
          <ErrorBox error={err} />
        </div>
      )}
    </Modal>
  );
}

function AdjustmentModal({ month, category, onClose, onDone }: { month: string; category: string; onClose: () => void; onDone: () => void }) {
  const { withReauth } = useSession();
  const [kind, setKind] = useState("refund");
  const [appliesOn, setAppliesOn] = useState(`${month}-01`);
  const [amount, setAmount] = useState("");
  const [target, setTarget] = useState("0");
  const [deduction, setDeduction] = useState("0");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const signed = (s: string) => s.replace(/[^0-9-]/g, "");
  return (
    <Modal
      title="源泉所得税の訂正を登録"
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !amount || !reason.trim()}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await withReauth(() =>
                  api("/api/accounting/withholding/adjustments", {
                    method: "POST",
                    idempotencyKey: newIdempotencyKey(),
                    body: { category, kind, applies_on: appliesOn, amount_delta: amount, target_delta: target || "0", deduction_delta: deduction || "0", reason },
                  }),
                );
                onDone();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            登録する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          元の支払いの明細は変更せず、訂正を記録した日の月に増減として集計します。減らす場合はマイナスで入力してください。
        </p>
        <Field label="種類">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            {ADJUSTMENT_KINDS.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="計上日（この日の月に集計）">
            <input className="input" type="date" value={appliesOn} onChange={(e) => setAppliesOn(e.target.value)} />
          </Field>
          <Field label="税額の増減（円）">
            <input className="input num" value={amount} onChange={(e) => setAmount(signed(e.target.value))} placeholder="例: -1021" />
          </Field>
          <Field label="対象額の増減（円）">
            <input className="input num" value={target} onChange={(e) => setTarget(signed(e.target.value))} />
          </Field>
          <Field label="控除額の増減（円）">
            <input className="input num" value={deduction} onChange={(e) => setDeduction(signed(e.target.value))} />
          </Field>
        </div>
        <Field label="理由（必須）">
          <textarea className="input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

function SalaryEntryModal({ month, onClose, onDone }: { month: string; onClose: () => void; onDone: () => void }) {
  const emps = useLoad<Employee[]>("/api/staff/employees");
  const [employee, setEmployee] = useState("");
  const [kind, setKind] = useState("regular");
  const [paidOn, setPaidOn] = useState(`${month}-25`);
  const [gross, setGross] = useState("");
  const [withholding, setWithholding] = useState("");
  const [headcount, setHeadcount] = useState("1");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="給与の源泉徴収を入力（給与計算ソフトの結果）"
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !gross || withholding === ""}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await api("/api/accounting/withholding/salary-entries", {
                  method: "POST",
                  idempotencyKey: newIdempotencyKey(),
                  body: { employee: employee ? Number(employee) : null, kind, paid_on: paidOn, gross_amount: gross, withholding_amount: withholding, headcount: Number(headcount) || 1, note },
                });
                onDone();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            登録する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="従業員（合計で入力する場合は空欄）">
          <select className="input" value={employee} onChange={(e) => setEmployee(e.target.value)}>
            <option value="">（合計で入力）</option>
            {emps.data?.map((e) => (
              <option key={e.id} value={e.id}>
                {e.display_name}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="区分">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="regular">俸給・給料等</option>
              <option value="bonus">賞与</option>
              <option value="daily_worker">日雇労務者の賃金（丙欄）</option>
            </select>
          </Field>
          <Field label="支給日">
            <input className="input" type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
          </Field>
          <Field label="支給額">
            <input className="input num" value={gross} onChange={(e) => setGross(e.target.value.replace(/[^0-9]/g, ""))} />
          </Field>
          <Field label="源泉徴収税額">
            <input className="input num" value={withholding} onChange={(e) => setWithholding(e.target.value.replace(/[^0-9]/g, ""))} />
          </Field>
          {!employee && (
            <Field label="人員（合計入力の場合）">
              <input className="input num" value={headcount} onChange={(e) => setHeadcount(e.target.value.replace(/[^0-9]/g, ""))} />
            </Field>
          )}
        </div>
        <Field label="メモ">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}
