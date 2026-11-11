"use client";

/** 会計ダッシュボード: 未処理の件数、今月の売上・経費・利益、店舗別の内訳、年度の状態、次にやること */
import Link from "next/link";
import { MonthPicker, RequireCaps } from "@/components/common";
import { Empty, ErrorBox, PageHeader, Spinner, Stat } from "@/components/ui";
import { monthRange, thisMonth } from "@/lib/accounting";
import { fmtDate, yen } from "@/lib/format";
import type { AccountingOverviewEx, OverviewCounts, ProfitLoss } from "@/lib/types_dashboard";
import { useLoad } from "@/lib/useLoad";
import { useState } from "react";

const FILING_LABEL: Record<string, string> = { white: "白色申告", blue_10: "青色申告（10万円控除）", blue_55: "青色申告（55万円控除）", blue_65: "青色申告（65万円控除）" };

export default function AccountingDashboardPage() {
  const q = useLoad<AccountingOverviewEx>("/api/accounting/overview");
  const [month, setMonth] = useState(thisMonth());
  const range = monthRange(month);
  const pl = useLoad<ProfitLoss>(q.data?.taxpayer ? `/api/accounting/pl?from=${range.from}&to=${range.to}` : null, [range.from, range.to, q.data?.taxpayer?.id]);

  return (
    <RequireCaps caps={["accounting.view"]}>
      <PageHeader title="会計ダッシュボード" subtitle="未処理のもの、今月の数字、年度の状態をひと目で。数字は締め済みの営業日と承認済みの経費から作った仕訳の集計です。" />
      {q.loading && <Spinner />}
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.data && !q.data.taxpayer && (
        <div className="card">
          <p className="font-semibold">この店舗の事業者が未設定です。</p>
          <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            <Link href="/accounting/settings" className="underline">
              会計設定
            </Link>
            で事業者を登録し、店舗に設定すると集計が始まります。
          </p>
        </div>
      )}
      {q.data?.taxpayer && (
        <div className="space-y-4">
          <TodoList counts={q.data.counts ?? {}} alerts={q.data.alerts} />
          <section>
            <h2 className="text-lg font-bold mb-2">未処理</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <Tile href="/accounting/expenses" label="確認待ちの領収書" value={q.data.counts?.pending_receipts} unit="件" />
              <Tile href="/accounting/expenses" label="差戻し中の領収書" value={q.data.counts?.returned_receipts} unit="件" />
              <Tile href="/accounting/bank" label="未照合の銀行明細" value={q.data.counts?.unmatched_bank} unit="件" />
              <Tile href="/closing" label="締めていない営業日" value={q.data.counts?.open_days} unit="日" />
              <Tile href="/accounting/books" label="仮勘定が残る仕訳" value={q.data.counts?.suspense_journals} unit="件" tone="danger" />
              <Tile href="/accounting/expenses" label="科目のない現金経費（90日）" value={q.data.counts?.unassigned_cash_expenses} unit="件" />
            </div>
          </section>
          <section className="card space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-bold">月の売上・経費・利益</h2>
              <MonthPicker value={month} onChange={setMonth} />
            </div>
            {pl.loading && <Spinner />}
            <ErrorBox error={pl.error} onRetry={() => pl.reload(false)} />
            {pl.data && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Stat label="売上（収益）" value={yen(pl.data.total_income)} />
                  <Stat label="売上原価" value={yen(pl.data.total_cogs)} />
                  <Stat label="経費（原価を含む）" value={yen(pl.data.total_expense)} />
                  <Stat label="利益" value={yen(pl.data.profit)} accent={Number(pl.data.profit) < 0 ? "danger" : "ok"} sub={Number(pl.data.suspense_total) !== 0 ? "仮勘定があるため未確定" : undefined} />
                </div>
                {pl.data.by_store.length > 0 ? (
                  <div className="scroll-x">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>店舗</th>
                          <th className="text-right">収益</th>
                          <th className="text-right">費用</th>
                          <th className="text-right">利益</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pl.data.by_store.map((s) => (
                          <tr key={s.store_id}>
                            <td>{s.store}</td>
                            <td className="text-right num">{yen(s.income)}</td>
                            <td className="text-right num">{yen(s.expense)}</td>
                            <td className="text-right num" style={{ color: Number(s.profit) < 0 ? "var(--color-danger)" : undefined }}>
                              {yen(s.profit)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty>この月の仕訳はまだありません。営業日を締めると自動で入ります。</Empty>
                )}
                <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {pl.data.notice}
                </p>
              </>
            )}
          </section>
          <section className="card space-y-2">
            <h2 className="text-lg font-bold">年度の状態</h2>
            {(q.data.counts?.fiscal_years ?? []).length === 0 && <Empty>年度はまだ作られていません。仕訳を作ると自動でできます。</Empty>}
            {(q.data.counts?.fiscal_years ?? []).map((fy) => (
              <div key={fy.id} className="card-2 flex flex-wrap items-center gap-3">
                <span className="text-xl font-bold num">{fy.year}年分</span>
                <span className={`badge ${fy.is_locked ? "badge-ok" : "badge-accent"}`}>{fy.status_label}</span>
                <span className="text-sm">{FILING_LABEL[fy.filing_type] ?? fy.filing_type}</span>
                <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {fmtDate(fy.start_date)} 〜 {fmtDate(fy.end_date)}
                </span>
                {fy.drift_warnings?.length > 0 && <span className="badge badge-warn">締め後の変更 {fy.drift_warnings.length} 件</span>}
                <span className="ml-auto flex gap-2">
                  <Link href="/accounting/yearend" className="btn btn-sm">
                    年間決算
                  </Link>
                  <Link href="/accounting/tax-return" className="btn btn-sm">
                    確定申告
                  </Link>
                </span>
              </div>
            ))}
          </section>
          {q.data.alerts.length > 0 && (
            <section className="card space-y-2">
              <h2 className="text-lg font-bold">源泉所得税の期限</h2>
              {q.data.alerts.map((a, i) => (
                <div key={i} className="card-2 flex flex-wrap items-center gap-3">
                  <span className={`badge ${a.level === "overdue" ? "badge-danger" : a.level === "soon" ? "badge-warn" : ""}`}>{a.status_label}</span>
                  <span>
                    {a.label}（{a.category_label}）
                  </span>
                  <span className="text-sm">納付期限 {fmtDate(a.due_date)}</span>
                  <span className="num">{yen(a.unpaid_total)}</span>
                  <Link href="/accounting" className="btn btn-sm ml-auto">
                    開く
                  </Link>
                </div>
              ))}
            </section>
          )}
        </div>
      )}
    </RequireCaps>
  );
}

function Tile({ href, label, value, unit, tone }: { href: string; label: string; value: number | undefined; unit: string; tone?: "danger" }) {
  const n = value ?? 0;
  return (
    <Link href={href} className="block">
      <Stat label={label} value={`${n.toLocaleString("ja-JP")} ${unit}`} accent={n > 0 ? (tone ?? "warn") : "ok"} sub={n > 0 ? "開いて確認" : "なし"} />
    </Link>
  );
}

function TodoList({ counts, alerts }: { counts: Partial<OverviewCounts>; alerts: AccountingOverviewEx["alerts"] }) {
  const items: { text: string; href: string }[] = [];
  if ((counts.open_days ?? 0) > 0) items.push({ text: `締めていない営業日が ${counts.open_days} 日あります。締めると売上と現金が帳簿に入ります。`, href: "/closing" });
  if ((counts.pending_receipts ?? 0) > 0) items.push({ text: `確認待ちの領収書が ${counts.pending_receipts} 件あります。科目を確かめて承認してください。`, href: "/accounting/expenses" });
  if ((counts.unmatched_bank ?? 0) > 0) items.push({ text: `未照合の銀行明細が ${counts.unmatched_bank} 件あります。カード入金・経費の支払いと結び付けてください。`, href: "/accounting/bank" });
  if ((counts.suspense_journals ?? 0) > 0) items.push({ text: `仮勘定（仮払金・仮受金）が残る仕訳が ${counts.suspense_journals} 件あります。内容を確かめて科目を直してください。`, href: "/accounting/books" });
  if ((counts.unassigned_cash_expenses ?? 0) > 0) items.push({ text: `科目の付いていない現金経費が ${counts.unassigned_cash_expenses} 件あります。領収書を登録して承認すると科目が付きます。`, href: "/accounting/expenses" });
  const overdue = alerts.filter((a) => a.level === "overdue").length;
  if (overdue) items.push({ text: `納付期限を過ぎた源泉所得税が ${overdue} 件あります。`, href: "/accounting" });
  if (!items.length) {
    return (
      <div className="card" style={{ borderColor: "var(--color-ok)" }}>
        <p className="font-semibold">✓ いま対応が必要なものはありません。</p>
      </div>
    );
  }
  return (
    <div className="card space-y-2" style={{ borderColor: "var(--color-warn)" }}>
      <h2 className="text-lg font-bold">次にやること</h2>
      <ol className="space-y-2">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-3">
            <span className="badge badge-warn">{i + 1}</span>
            <span className="flex-1">{it.text}</span>
            <Link href={it.href} className="btn btn-sm">
              開く
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}
