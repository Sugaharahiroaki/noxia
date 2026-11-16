"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { RequireCaps } from "@/components/common";
import { amt, cnt, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtDate, fmtTime } from "@/lib/format";
import type { SlipDetail } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";

export default function SlipDetailPage() {
  return (
    <RequireCaps caps={["sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const { id } = useParams<{ id: string }>();
  const q = useLoad<SlipDetail>(`/api/reports/slips/${id}`);
  const d = q.data;
  if (q.loading || !d) return q.error ? <ErrorBox error={q.error} onRetry={() => q.reload(false)} /> : <Spinner />;
  const equal = d.settings.cast_sales_split_mode === "equal";

  return (
    <div>
      <PageHeader
        title={`伝票 #${d.slip_number}（${d.table_name}）`}
        subtitle={
          <>
            営業日 {fmtDate(d.business_date)} ・ 入店 {fmtTime(d.opened_at)} − 退店 {d.closed_at ? fmtTime(d.closed_at) : "--"}
            {d.minutes !== null && `（${d.minutes}分）`} ・ 客数 {d.guest_count}名{d.customer && ` ・ ${d.customer}`}
          </>
        }
        actions={
          <>
            <Link href="/reports/slips" className="btn btn-sm">
              ← 伝票一覧
            </Link>
            <Link href="/sales" className="btn btn-sm">
              訂正（取消・返金）は売上・会計履歴で
            </Link>
          </>
        }
      />
      <ReportNav />
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className={`badge ${d.status === "open" ? "badge-accent" : d.status === "cancelled" ? "badge-danger" : "badge-ok"}`}>{d.status_label}</span>
        {d.courses.length > 0 && <span className="badge">セット構成: {[...new Set(d.courses)].join("・")}</span>}
        {d.tags.map((t) => (
          <span key={t.name} className="badge" style={t.color ? { background: t.color, color: "#fff", borderColor: t.color } : undefined}>
            {t.name}
          </span>
        ))}
        {d.status === "cancelled" && d.cancel_reason && <span>取消理由: {d.cancel_reason}</span>}
        {d.status === "open" && <span style={{ color: "var(--color-muted)" }}>営業中の伝票です。会計が確定するまで金額は出ません。</span>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="xl:col-span-2 space-y-3">
          <ReportTable label="明細">
            <thead>
              <tr>
                <th>区分</th>
                <th>商品</th>
                <th>キャスト</th>
                <th className="r">単価</th>
                <th className="r">税</th>
                <th className="r">数量</th>
                <th className="r">計</th>
                <th>会計</th>
              </tr>
            </thead>
            <tbody>
              {d.lines.map((l) => (
                <tr key={l.id} style={l.status === "cancelled" ? { opacity: 0.55 } : undefined}>
                  <td>{l.line_type_label}</td>
                  <td>
                    {l.name}
                    {l.status === "cancelled" && <span className="badge badge-danger ml-1">取消{l.cancel_reason ? `: ${l.cancel_reason}` : ""}</span>}
                  </td>
                  <td>{l.cast.join("・") || "--"}</td>
                  <td className="r">{amt(l.unit_price)}</td>
                  <td className="r">
                    {Number(l.tax_rate)}%{l.tax_included ? "（内）" : "（外）"}
                  </td>
                  <td className="r">{cnt(l.quantity)}</td>
                  <td className="r font-semibold">{amt(l.amount)}</td>
                  <td>{l.checkout_number ? `#${l.checkout_number}${l.paid ? "" : "（取消）"}` : l.status === "active" ? "未会計" : "--"}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>

          <section className="card" aria-label="会計">
            <h2 className="font-bold mb-2">会計（確定した会計の合計）</h2>
            <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1 num">
              <Cell label="小計" value={amt(d.subtotal)} />
              <Cell label="値引き" value={amt(d.discount)} />
              <Cell label="サービス料" value={amt(d.service_charge)} />
              <Cell label="合計" value={amt(d.total)} strong />
              <Cell label="（内消費税）" value={amt(d.tax)} />
              <Cell label="現金" value={amt(d.cash)} />
              <Cell label="カード" value={amt(d.card)} />
              <Cell label="売掛" value={amt(d.receivable)} />
              <Cell label="預かり合計" value={amt(d.tendered)} />
              <Cell label="お釣り" value={amt(d.change)} />
              <Cell label="返金" value={amt(d.refunds)} />
            </dl>
            {d.checkouts.length > 1 && (
              <ul className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
                {d.checkouts.map((c) => (
                  <li key={c.id}>
                    会計#{c.number}（{c.status_label}・{c.kind === "partial" ? "分割" : "全体"}）{fmtTime(c.confirmed_at)} {amt(c.total)} 円 … {c.payments.map((p) => `${p.method_label} ${amt(p.amount)}`).join("、")}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <section className="space-y-2">
          <h2 className="font-bold">キャスト別の売上小計</h2>
          <ReportTable label="キャスト別の売上小計">
            <thead>
              <tr>
                <th>キャスト</th>
                <th className="r">呼び小計</th>
                <th className="r">呼び本計</th>
                <th className="r">同伴小計</th>
                <th className="r">同伴本計</th>
                <th className="r">本指名</th>
                <th className="r">場内</th>
                <th className="r">同伴</th>
              </tr>
            </thead>
            <tbody>
              {d.cast_sales.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ color: "var(--color-muted)" }}>
                    指名・同伴のキャストはいません（または未会計です）。
                  </td>
                </tr>
              )}
              {d.cast_sales.map((c) => (
                <tr key={c.employee_id}>
                  <td className="font-semibold">{c.employee_name}</td>
                  <td className="r">{amt(c.yobi_subtotal)}</td>
                  <td className="r">{amt(c.yobi_total)}</td>
                  <td className="r">{amt(c.douhan_subtotal)}</td>
                  <td className="r">{amt(c.douhan_total)}</td>
                  <td className="r">{cnt(c.nomination_count)}</td>
                  <td className="r">{cnt(c.jounai_count)}</td>
                  <td className="r">{cnt(c.douhan_count)}</td>
                </tr>
              ))}
            </tbody>
          </ReportTable>
          <ReportNote>
            指名（本指名・場内）のある伝票の小計・本計を「呼び」として、同伴のある伝票の小計・本計を「同伴」として、そのキャストに計上します。 対象のキャストが複数のときは
            <strong>{equal ? "均等割り（仮）" : "全員に全額"}</strong>
            です（余りは先頭の人に足します）。指名回数は{d.settings.nomination_count_mode === "per_set" ? `セット数ぶん（この伝票は ${d.set_count} セット）` : "1 伝票 1 回"}。設定は伝票一覧の下で変えられます。
          </ReportNote>
        </section>
      </div>
    </div>
  );
}

function Cell({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <dt style={{ color: "var(--color-muted)" }}>{label}</dt>
      <dd className={strong ? "text-xl font-bold" : "font-semibold"}>{value}</dd>
    </div>
  );
}
