"use client";

/** 自店舗の領収書の登録状況（スタッフ・店長）。科目・税務情報は出ない。 */
import Link from "next/link";
import { useState } from "react";
import { RequireCaps, Tabs } from "@/components/common";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { fmtDate, fmtDateTime, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { PAYMENT_METHODS, STATUS_BADGE, type PendingCounts, type Receipt, type ReceiptList } from "@/lib/types_expenses";
import { useLoad } from "@/lib/useLoad";

const TABS = [
  { key: "returned", label: "差戻し" },
  { key: "pending,ocr_running,draft", label: "確認待ち" },
  { key: "approved", label: "承認済み" },
  { key: "rejected,voided", label: "却下・取消" },
];

export default function ReceiptsPage() {
  const { can } = useSession();
  const counts = useLoad<PendingCounts>("/api/expenses/receipts/pending-count", [], { intervalMs: 30000 });
  const [tab, setTab] = useState(TABS[1].key);
  const q = useLoad<ReceiptList>(`/api/expenses/receipts?status=${tab}&page=1&page_size=100`, [tab], { intervalMs: 30000 });
  const c = counts.data;
  const tabs = TABS.map((t) => ({
    ...t,
    badge: c ? t.key.split(",").reduce((s, k) => s + (c[k as keyof PendingCounts] ?? 0), 0) : undefined,
    tone: t.key === "returned" ? ("danger" as const) : undefined,
  }));
  return (
    <RequireCaps anyCaps={["expenses.view_store", "accounting.view"]}>
      <PageHeader
        title="領収書・経費登録"
        subtitle="撮った領収書の状態です。「差戻し」は理由を見て直して、もう一度登録してください。"
        actions={
          can("expenses.submit") ? (
            <Link href="/receipts/new" className="btn btn-lg btn-primary">
              📷 領収書を撮る
            </Link>
          ) : null
        }
      />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {q.loading && <Spinner />}
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.data && q.data.items.length === 0 && <Empty>この状態の領収書はありません。</Empty>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {q.data?.items.map((r: Receipt) => (
          <Link key={r.id} href={`/receipts/${r.id}`} className="card block" style={{ borderColor: r.status === "returned" ? "var(--color-danger)" : undefined }}>
            <div className="flex items-center justify-between gap-2">
              <span className={STATUS_BADGE[r.status]} style={{ fontSize: "1rem" }}>
                {r.status_label}
              </span>
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                {r.receipt_date ? fmtDate(r.receipt_date) : "日付なし"}
              </span>
            </div>
            <div className="flex items-end justify-between mt-2">
              <div>
                <div className="text-lg font-semibold">{r.vendor || "（支払先なし）"}</div>
                <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {PAYMENT_METHODS.find((m) => m.key === r.payment_method)?.label}
                  {r.memo ? ` ・ ${r.memo}` : ""}
                </div>
              </div>
              <div className="text-2xl font-bold num">{r.total_amount ? yen(r.total_amount) : "—"}</div>
            </div>
            {r.status === "returned" && r.return_reason && (
              <p className="mt-2 font-semibold" style={{ color: "var(--color-danger)" }}>
                差戻しの理由: {r.return_reason}
              </p>
            )}
            {r.status === "rejected" && r.reject_reason && (
              <p className="mt-2 text-sm" style={{ color: "var(--color-muted)" }}>
                却下の理由: {r.reject_reason}
              </p>
            )}
            {(r.ocr.status === "failed" || r.ocr.status === "skipped") && r.status !== "approved" && (
              <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
                読み取りなし（手入力）
              </p>
            )}
            <div className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
              {r.submitted_by_name} ・ {fmtDateTime(r.submitted_at ?? r.created_at)}
            </div>
          </Link>
        ))}
      </div>
    </RequireCaps>
  );
}
