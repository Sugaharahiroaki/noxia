"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtDate, fmtDateTime, yen } from "@/lib/format";
import type { Handwrite } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

/** 手書き領収書への転記用。会計で確定した金額をそのまま大きく表示する（再計算しない）。 */
export default function HandwritePage() {
  const { id } = useParams<{ id: string }>();
  const q = useLoad<Handwrite>(`/api/checkouts/${id}/handwrite`);
  if (q.loading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data;
  const hasReceivable = Number(d.receivable_amount) > 0;
  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <PageHeader
        title="手書き領収書への転記用"
        subtitle={
          <>
            会計#{d.checkout_number} ・ 伝票#{d.slip_number} ・ {d.table_name} ・ 営業日 {fmtDate(d.business_date)} ・ 確定 {fmtDateTime(d.confirmed_at)}
            {d.status === "voided" && <span className="ml-2 badge badge-danger">この会計は取消済みです</span>}
          </>
        }
        actions={
          <>
            <Link href="/floor" className="btn">
              卓一覧へ
            </Link>
            <Link href="/sales" className="btn">
              会計履歴へ
            </Link>
          </>
        }
      />
      <div className={`card-2 ${d.kind === "partial" ? "" : ""}`} style={d.kind === "partial" ? { borderColor: "var(--color-warn)" } : undefined}>
        <p className="font-semibold">{d.scope_note}</p>
        {d.kind === "partial" && (
          <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            伝票全体の会計合計（確定済み分）: {yen(d.visit_total_all_checkouts)}
            {d.other_checkouts.length > 0 && <> ・ 他の会計: {d.other_checkouts.map((c) => `#${c.number} ${yen(c.total)}`).join("、")}</>}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Big label="税込合計（領収金額）" value={yen(d.total)} primary />
        <Big label="税抜金額" value={yen(d.total_excl_tax)} />
        {d.tax_breakdown.map((t) => (
          <Big key={t.rate} label={`消費税（${Number(t.rate)}% 対象 ${yen(t.taxable_excl)}）`} value={yen(t.tax)} />
        ))}
        {Number(d.service_charge) > 0 && <Big label="うちサービス料（税込）" value={yen(d.service_charge)} small />}
        {Number(d.discount_total) > 0 && <Big label="値引き（反映済み）" value={"−" + yen(d.discount_total)} small />}
        {Number(d.rounding_adjustment) !== 0 && <Big label="端数調整（反映済み）" value={yen(d.rounding_adjustment, { sign: true })} small />}
      </div>

      <h2 className="font-bold text-lg mt-2">お支払い（領収書に書く金額）</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Big label="現金支払額" value={yen(d.cash_paid)} />
        <Big label="カード支払額" value={yen(d.card_paid)} />
        {hasReceivable && <Big label="未収（ツケ）額 ※領収額に含めない" value={yen(d.receivable_amount)} warn />}
      </div>
      {Number(d.cash_tendered) > 0 && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          参考（領収書には書かない）: 現金の預り額 {yen(d.cash_tendered)} ・ 釣銭 {yen(d.cash_change)}
        </p>
      )}

      <details className="card-2">
        <summary className="cursor-pointer">この会計の明細（{d.lines.length}件）</summary>
        <ul className="mt-2 space-y-1 num">
          {d.lines.map((l, i) => (
            <li key={i} className="flex justify-between">
              <span>{l.name}</span>
              <span>{yen(l.amount)}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Big({ label, value, primary, small, warn }: { label: string; value: string; primary?: boolean; small?: boolean; warn?: boolean }) {
  return (
    <div className="card" style={primary ? { borderColor: "var(--color-accent)" } : warn ? { borderColor: "var(--color-warn)" } : undefined}>
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        {label}
      </div>
      <div className={`font-bold num ${primary ? "text-5xl" : small ? "text-2xl" : "text-4xl"}`} style={warn ? { color: "var(--color-warn)" } : undefined}>
        {value}
      </div>
    </div>
  );
}
