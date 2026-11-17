"use client";

import { Tx } from "@/lib/terms";
import { useParams, useRouter } from "next/navigation";
import { ErrorBox, Spinner } from "@/components/ui";
import { yen } from "@/lib/format";
import type { OrderLine, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

const FEE = new Set(["nomination", "jounai", "yobi", "douhan"]);
const SET = new Set(["course_set", "extension", "course_change"]);

/**
 * 明細。「同伴／指名」「セット」「注文」を横に並べる。小計・サービス料・税・合計は API の quote をそのまま出す（画面で計算しない）。
 * 見るだけの画面で、開いても何も変わらない。操作は「戻る」と「会計」。
 */
export default function BillPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const visitQ = useLoad<Visit>(`/api/visits/${id}`);
  const visit = visitQ.data;

  if (visitQ.loading || !visit) return visitQ.error ? <ErrorBox error={visitQ.error} onRetry={() => visitQ.reload(false)} /> : <Spinner label="明細を読み込み中…" />;

  // 未会計の有効な明細（会計・ホール・卓詳細の金額と同じ範囲）
  const lines = visit.lines.filter((l) => l.status === "active" && l.checkout === null);
  const fee = lines.filter((l) => FEE.has(l.line_type));
  const sets = lines.filter((l) => SET.has(l.line_type));
  const orders = lines.filter((l) => l.line_type === "product");
  const discounts = lines.filter((l) => l.line_type === "discount");
  const q = visit.quote;
  const paid = visit.lines.filter((l) => l.status === "active" && l.checkout !== null).length;
  const open = visit.status === "open";

  return (
    <div className="flex flex-col gap-2" style={{ height: "calc(100dvh - 64px - 34px)", minHeight: 480 }}>
      <div className="flex items-center gap-3">
        <button className="btn btn-lg" onClick={() => router.replace(`/visits/${visit.id}`)}>
          ← 戻る
        </button>
        <div>
          <div className="text-3xl font-bold leading-none">{visit.table_name} の明細</div>
          <div className="text-sm mt-0.5" style={{ color: "var(--color-muted)" }}>
            伝票#{visit.slip_number} ・ 在席 {visit.present_count}名（来店 {visit.guest_count}名）
            {paid > 0 && ` ・ 会計済みの明細 ${paid}件は含みません`}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 flex-1 min-h-0">
        <Column title="同伴／指名" lines={fee} empty="指名・同伴はありません" showTargets />
        <Column title="セット" lines={sets} empty="セットはありません" />
        <Column title="注文" lines={orders} empty="注文はありません" showTargets />
      </div>

      <div className="card flex items-end gap-4" style={{ padding: "0.6rem 0.9rem" }} aria-label="金額">
        <div className="grid grid-cols-4 gap-x-6 gap-y-0.5 num text-lg flex-1">
          <Cell label="小計" value={yen(q?.subtotal)} testid="bill-subtotal" />
          <Cell label="値引き" value={q && Number(q.discount_total) > 0 ? `−${yen(q.discount_total)}` : "—"} hint={discounts.map((d) => d.discount_reason).filter(Boolean).join("・")} />
          <Cell label={q?.by_method?.cash ? `サービス料（標準 ${Number(q.service_charge_rate ?? 0)}%）` : "サービス料"} value={q && Number(q.service_charge) > 0 ? yen(Number(q.service_charge) + Number(q.service_charge_tax)) : "—"} />
          <Cell label="消費税（うち）" value={yen(q?.tax_total)} />
        </div>
        <div className="text-right">
          <div className="text-sm" style={{ color: "var(--color-muted)" }}>
            合計（税込{q?.by_method?.cash ? "・標準の料率" : ""}）
            {q && Number(q.rounding_adjustment) !== 0 && <span className="ml-2">端数調整 {yen(q.rounding_adjustment, { sign: true })}</span>}
          </div>
          <div className="text-5xl font-bold num leading-none" data-testid="bill-total">
            {yen(q?.total)}
          </div>
          {q?.by_method?.cash && q.by_method.card && (
            // 支払方法でサービス料率が変わる店: 上は標準の料率の金額。現金・カードそれぞれの金額も出す
            <div className="text-base num mt-1" data-testid="bill-total-by-method">
              現金 {yen(q.by_method.cash.total)}（{Number(q.by_method.cash.rate)}%）・ カード {yen(q.by_method.card.total)}（{Number(q.by_method.card.rate)}%）
            </div>
          )}
        </div>
        <button className="btn btn-xl" style={{ minWidth: 130 }} onClick={() => router.replace(`/visits/${visit.id}`)}>
          戻る
        </button>
        <button className="btn btn-xl btn-ok" style={{ minWidth: 170 }} disabled={!open || lines.length === 0} onClick={() => router.push(`/visits/${visit.id}/checkout`)}>
          会計
        </button>
      </div>
    </div>
  );
}

function Cell({ label, value, hint, testid }: { label: string; value: string; hint?: string; testid?: string }) {
  return (
    <div>
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        {label}
      </div>
      <div className="font-bold" data-testid={testid}>
        {value}
      </div>
      {hint && (
        <div className="text-xs truncate" style={{ color: "var(--color-muted)" }}>
          {hint}
        </div>
      )}
    </div>
  );
}

function Column({ title, lines, empty, showTargets }: { title: string; lines: OrderLine[]; empty: string; showTargets?: boolean }) {
  return (
    <section className="flex flex-col min-h-0" style={{ background: "var(--color-panel)", border: "1.5px solid var(--color-border)", borderRadius: 14 }} aria-label={title}>
      <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px] flex justify-between">
        <span>
          <Tx>{title}</Tx>
        </span>
        <span className="num">{lines.length}件</span>
      </h2>
      <div className="flex-1 overflow-y-auto p-1.5 space-y-1">
        {lines.length === 0 && (
          <p className="p-2" style={{ color: "var(--color-muted)" }}>
            {empty}
          </p>
        )}
        {lines.map((l) => (
          <div key={l.id} className="rounded-lg px-2 py-1.5" style={{ background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-bold leading-tight">{l.name}</span>
              <span className="font-bold num whitespace-nowrap">{yen(l.amount)}</span>
            </div>
            <div className="flex justify-between text-sm num" style={{ color: "var(--color-muted)" }}>
              <span>
                {yen(l.unit_price)} × {Number(l.quantity)}
              </span>
              {showTargets && l.line_type === "product" && l.back_targets.length > 0 && <span>{l.back_targets.map((t) => t.employee_name).join("・")}</span>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
