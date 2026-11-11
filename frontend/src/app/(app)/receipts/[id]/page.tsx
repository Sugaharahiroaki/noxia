"use client";

/** 領収書 1 件（スタッフ）: 原本と内容。差戻し・下書きなら直して再登録できる。 */
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { FieldErrors, ReasonPicker, RequireCaps } from "@/components/common";
import { FileViewer } from "@/components/FileViewer";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, ErrorBox, Field, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { fmtDateTime, yen } from "@/lib/format";
import { PAYMENT_METHODS, STATUS_BADGE, type Receipt, type ReceiptEvent, type ReceiptPaymentMethod } from "@/lib/types_expenses";
import { useLoad } from "@/lib/useLoad";

export default function ReceiptDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const q = useLoad<Receipt>(`/api/expenses/receipts/${id}`, [id]);
  const events = useLoad<ReceiptEvent[]>(`/api/expenses/receipts/${id}/events`, [id]);
  const [form, setForm] = useState<{ receipt_date: string; total_amount: string; vendor: string; payment_method: ReceiptPaymentMethod; memo: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirmVoid, setConfirmVoid] = useState(false);

  useEffect(() => {
    const r = q.data;
    if (r && !form) {
      setForm({ receipt_date: r.receipt_date ?? "", total_amount: r.total_amount ? String(Math.round(Number(r.total_amount))) : "", vendor: r.vendor, payment_method: r.payment_method, memo: r.memo });
    }
  }, [q.data, form]);

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const r = q.data;
  if (!r || !form) return null;

  async function save(andSubmit: boolean) {
    if (!r || !form) return;
    setBusy(true);
    setErr(null);
    try {
      const r1 = await api<Receipt>(`/api/expenses/receipts/${r.id}`, { method: "PATCH", body: { ...form, version: r.version } });
      if (andSubmit) {
        await api(`/api/expenses/receipts/${r.id}/submit`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { version: r1.version } });
        toast.show({ message: "もう一度登録しました（確認待ち）" });
        router.replace("/receipts");
        return;
      }
      toast.show({ message: "保存しました" });
      await q.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <RequireCaps anyCaps={["expenses.view_store", "accounting.view"]}>
      <PageHeader
        title={`領収書 ${r.vendor || ""}`}
        subtitle={
          <span className="flex items-center gap-2">
            <span className={STATUS_BADGE[r.status]}>{r.status_label}</span>
            <span>{r.store_name}</span>
          </span>
        }
        actions={
          <button className="btn" onClick={() => router.push("/receipts")}>
            一覧へ
          </button>
        }
      />
      <ErrorBox error={err} />
      {err instanceof ApiError && err.details ? <FieldErrors details={err.details} /> : null}
      {r.status === "returned" && (
        <div className="card mb-3" style={{ borderColor: "var(--color-danger)" }}>
          <p className="font-bold text-lg" style={{ color: "var(--color-danger)" }}>
            差し戻されました: {r.return_reason}
          </p>
          <p className="text-sm">内容を直して「もう一度登録する」を押してください。</p>
        </div>
      )}
      <div className="grid lg:grid-cols-2 gap-4">
        <FileViewer path={`/api/expenses/receipts/${r.id}/file?inline=1`} name={r.file_name} />
        <div className="space-y-3">
          {r.ocr.warnings.map((w) => (
            <p key={w.code} className="badge badge-warn">
              {w.message}
            </p>
          ))}
          <Field label="金額（税込）">
            <input className="input input-lg" inputMode="numeric" value={form.total_amount} disabled={!r.can_edit} onChange={(e) => setForm({ ...form, total_amount: e.target.value.replace(/[^\d]/g, "") })} />
          </Field>
          <Field label="日付">
            <input type="date" className="input" value={form.receipt_date} disabled={!r.can_edit} onChange={(e) => setForm({ ...form, receipt_date: e.target.value })} />
          </Field>
          <Field label="支払先">
            <input className="input" value={form.vendor} disabled={!r.can_edit} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
          </Field>
          <Field label="支払方法">
            <div className="grid grid-cols-2 gap-2">
              {PAYMENT_METHODS.map((m) => (
                <button key={m.key} type="button" className={`btn ${form.payment_method === m.key ? "btn-primary" : ""}`} disabled={!r.can_edit} onClick={() => setForm({ ...form, payment_method: m.key })}>
                  {m.label}
                </button>
              ))}
            </div>
          </Field>
          <Field label="メモ">
            {r.can_edit ? (
              <ReasonPicker options={["氷・レモン・割り物", "お菓子・乾き物", "備品・消耗品", "お酒の仕入", "タクシー・交通費"]} value={form.memo} onChange={(v) => setForm({ ...form, memo: v })} />
            ) : (
              <div className="card-2">{r.memo || "—"}</div>
            )}
          </Field>
          {r.tax_breakdown.length > 0 && (
            <div className="card-2">
              <div className="label">税率の内訳</div>
              {r.tax_breakdown.map((b, i) => (
                <div key={i} className="flex justify-between num">
                  <span>{b.rate}% 対象</span>
                  <span>{yen(b.taxable_incl)}</span>
                </div>
              ))}
            </div>
          )}
          {r.can_edit && (
            <div className="grid grid-cols-2 gap-3 pt-2">
              <button className="btn btn-lg" disabled={busy} onClick={() => save(false)}>
                保存だけ
              </button>
              <button className="btn btn-lg btn-primary" disabled={busy || !form.total_amount || !form.receipt_date} onClick={() => save(true)}>
                {r.status === "returned" ? "もう一度登録する" : "登録する"}
              </button>
              <button className="btn btn-ghost col-span-2" disabled={busy} onClick={() => setConfirmVoid(true)}>
                この登録を取り消す
              </button>
            </div>
          )}
          <div className="card-2">
            <div className="label">履歴</div>
            <ul className="text-sm space-y-1">
              {(events.data ?? []).map((e) => (
                <li key={e.id}>
                  {fmtDateTime(e.at)} {EVENT_LABELS[e.action] ?? e.action} {e.actor_name}
                  {e.reason ? ` — ${e.reason}` : ""}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
      {confirmVoid && (
        <ConfirmDialog
          title="この登録を取り消しますか"
          message="取り消した領収書は帳簿に入りません。撮り直すときは新しく登録してください。"
          confirmLabel="取り消す"
          danger
          busy={busy}
          onCancel={() => setConfirmVoid(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api(`/api/expenses/receipts/${r.id}/void`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { reason: "登録者による取消" } });
              toast.show({ message: "取り消しました", tone: "info" });
              router.replace("/receipts");
            } catch (e) {
              setErr(e);
              setConfirmVoid(false);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
    </RequireCaps>
  );
}

const EVENT_LABELS: Record<string, string> = {
  create: "撮影・登録開始",
  ocr_done: "AI 読取",
  ocr_failed: "AI 読取に失敗",
  ocr_skipped: "AI 読取なし",
  edit: "内容を変更",
  submit: "登録",
  resubmit: "再登録",
  approve: "承認",
  return: "差戻し",
  reject: "却下",
  void: "取消",
  correct: "承認後の訂正",
};
