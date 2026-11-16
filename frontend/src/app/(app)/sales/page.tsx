"use client";

import Link from "next/link";
import { useState } from "react";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Stat, type SaveState } from "@/components/ui";
import { PinPad } from "@/components/PinPad";
import { api, ApiError, downloadFile, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtId } from "@/components/ReportKit";
import { fmtDate, fmtTime, LINE_TYPE_LABELS, todayJst, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Checkout, Receivable } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type History = {
  from: string;
  to: string;
  summary: { checkout_count: number; voided_count: number; total: string; cash: string; card: string; receivable: string; refunds: string; guest_count: number; avg_per_guest: string | null };
  checkouts: Checkout[];
};

export default function SalesPage() {
  const { can } = useSession();
  const [range, setRange, bd] = useDateRange();
  const from = range?.from ?? "";
  const to = range?.to ?? "";
  const [tab, setTab] = useState<"checkouts" | "receivables">("checkouts");
  const hist = useLoad<History>(range ? `/api/sales/history?from=${from}&to=${to}` : null);
  const recv = useLoad<Receivable[]>(tab === "receivables" ? "/api/receivables" : null);
  const [detail, setDetail] = useState<Checkout | null>(null);
  const [voidTarget, setVoidTarget] = useState<Checkout | null>(null);
  const [refundTarget, setRefundTarget] = useState<Checkout | null>(null);
  const [collectTarget, setCollectTarget] = useState<Receivable | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [err, setErr] = useState<unknown>(null);

  async function reloadAll() {
    await hist.reload();
    if (tab === "receivables") await recv.reload();
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="売上・会計履歴"
        subtitle="営業日単位の集計です。売上日と実際の支払・返金日時は別に保持しています。"
        actions={
          <>
            <SaveStatus state={save} />
            {can("export.sales") && (
              <button className="btn btn-sm" onClick={() => downloadFile(`/api/exports/sales.csv?from=${from}&to=${to}`).catch(setErr)}>
                売上CSV
              </button>
            )}
            {can("export.sales") && (
              <button className="btn btn-sm" onClick={() => downloadFile(`/api/exports/payments.csv?from=${from}&to=${to}`).catch(setErr)}>
                支払CSV
              </button>
            )}
          </>
        }
      />
      <DateRangeBar range={range} onChange={setRange} businessDate={bd} />
      <ErrorBox error={hist.error || err} />
      {hist.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2">
          <Stat label="売上合計" value={yen(hist.data.summary.total)} />
          <Stat label="会計件数" value={hist.data.summary.checkout_count} sub={hist.data.summary.voided_count ? `取消 ${hist.data.summary.voided_count}` : undefined} />
          <Stat label="客数" value={hist.data.summary.guest_count} sub={hist.data.summary.avg_per_guest ? `客単価 ${yen(hist.data.summary.avg_per_guest)}` : undefined} />
          <Stat label="現金" value={yen(hist.data.summary.cash)} />
          <Stat label="カード" value={yen(hist.data.summary.card)} />
          <Stat label="未収" value={yen(hist.data.summary.receivable)} />
          <Stat label="返金" value={yen(hist.data.summary.refunds)} />
        </div>
      )}
      <div className="flex gap-2">
        <button className={`btn btn-sm ${tab === "checkouts" ? "btn-primary" : ""}`} onClick={() => setTab("checkouts")}>
          会計一覧
        </button>
        <button className={`btn btn-sm ${tab === "receivables" ? "btn-primary" : ""}`} onClick={() => setTab("receivables")}>
          未収（ツケ）
        </button>
      </div>

      {tab === "checkouts" && (
        <div className="card">
          {hist.loading && <Spinner />}
          {hist.data?.checkouts.length === 0 && <Empty>この期間の会計はありません</Empty>}
          <div className="scroll-x">
            <table className="table num">
              <thead>
                <tr>
                  <th>営業日</th>
                  <th>会計ID</th>
                  <th>伝票ID</th>
                  <th>卓</th>
                  <th>確定</th>
                  <th>請求額</th>
                  <th>支払</th>
                  <th>状態</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {hist.data?.checkouts.map((c) => (
                  <tr key={c.id} style={{ opacity: c.status === "voided" ? 0.55 : 1 }}>
                    <td>{fmtDate(c.business_date)}</td>
                    <td style={{ fontFamily: "ui-monospace, Consolas, monospace" }}>{fmtId(c.public_id)}</td>
                    <td style={{ fontFamily: "ui-monospace, Consolas, monospace" }}>{fmtId(c.visit_public_id)}</td>
                    <td>{c.visit_table_name}</td>
                    <td>{fmtTime(c.confirmed_at)}</td>
                    <td className="font-bold">{yen(c.total)}</td>
                    <td>{c.payments.map((p) => `${p.method_label} ${yen(p.amount)}`).join(" / ")}</td>
                    <td>
                      {c.status === "voided" ? <span className="badge badge-danger">取消</span> : <span className="badge badge-ok">確定</span>}
                      {c.kind === "partial" && <span className="badge ml-1">分割</span>}
                      {c.refunds.length > 0 && <span className="badge badge-warn ml-1">返金あり</span>}
                    </td>
                    <td className="whitespace-nowrap">
                      <button className="btn btn-sm" onClick={() => setDetail(c)}>
                        詳細
                      </button>{" "}
                      <Link href={`/checkouts/${c.id}/handwrite`} className="btn btn-sm">
                        転記用
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "receivables" && (
        <div className="card">
          {recv.loading && <Spinner />}
          <ErrorBox error={recv.error} />
          {recv.data?.length === 0 && <Empty>未収はありません</Empty>}
          <div className="scroll-x">
            <table className="table num">
              <thead>
                <tr>
                  <th>営業日</th>
                  <th>会計#</th>
                  <th>相手</th>
                  <th>未収額</th>
                  <th>回収済</th>
                  <th>残額</th>
                  <th>状態</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {recv.data?.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.business_date)}</td>
                    <td>{r.checkout_number}</td>
                    <td>{r.customer_name || r.debtor_name || "—"}</td>
                    <td>{yen(r.amount)}</td>
                    <td>{yen(r.collected_amount)}</td>
                    <td className="font-bold">{yen(r.remaining)}</td>
                    <td>{r.status === "open" ? <span className="badge badge-warn">未回収</span> : r.status === "collected" ? <span className="badge badge-ok">回収済</span> : <span className="badge">{r.status}</span>}</td>
                    <td>
                      {r.status === "open" && can("floor.operate") && (
                        <button className="btn btn-sm btn-primary" onClick={() => setCollectTarget(r)}>
                          回収を記録
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
            未収の回収は新しい売上として計上されません（売上は会計時の営業日に計上済み。回収は現金出納に「未収回収」として入ります）。
          </p>
        </div>
      )}

      {detail && (
        <Modal
          title={`会計#${detail.number} の詳細`}
          onClose={() => setDetail(null)}
          wide
          footer={
            <>
              {detail.status === "confirmed" && can("sales.void") && (
                <>
                  <button className="btn" onClick={() => setRefundTarget(detail)}>
                    一部返金
                  </button>
                  <button className="btn btn-danger" onClick={() => setVoidTarget(detail)}>
                    会計を取消
                  </button>
                </>
              )}
              <Link href={`/checkouts/${detail.id}/handwrite`} className="btn btn-primary">
                手書き領収書用の金額
              </Link>
            </>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 num">
            <div className="space-y-1">
              <div>
                伝票#{detail.visit_slip_number} ・ {detail.visit_table_name} ・ 営業日 {fmtDate(detail.business_date)}
              </div>
              <div>
                確定 {fmtTime(detail.confirmed_at)} {detail.confirmed_by_name && `(${detail.confirmed_by_name})`}
              </div>
              {detail.status === "voided" && (
                <div style={{ color: "var(--color-danger)" }}>
                  取消 {fmtTime(detail.voided_at)} {detail.voided_by_name}: {detail.void_reason}
                </div>
              )}
              <div className="pt-2 border-t" style={{ borderColor: "var(--color-border)" }}>
                {detail.lines.map((l) => (
                  <div key={l.id} className="flex justify-between text-sm">
                    <span>
                      <span className="badge mr-1">{LINE_TYPE_LABELS[l.line_type]}</span>
                      {l.name}
                      {Number(l.quantity) !== 1 && ` ×${Number(l.quantity)}`}
                    </span>
                    <span>{yen(l.amount)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="space-y-1">
              <div className="flex justify-between">
                <span>明細合計</span>
                <span>{yen(detail.subtotal)}</span>
              </div>
              {toInt(detail.discount_total) > 0 && (
                <div className="flex justify-between">
                  <span>値引き</span>
                  <span>−{yen(detail.discount_total)}</span>
                </div>
              )}
              {toInt(detail.service_charge) > 0 && (
                <div className="flex justify-between">
                  <span>サービス料</span>
                  <span>{yen(detail.service_charge)}</span>
                </div>
              )}
              <div className="flex justify-between" style={{ color: "var(--color-muted)" }}>
                <span>うち消費税</span>
                <span>{yen(detail.tax_total)}</span>
              </div>
              {toInt(detail.rounding_adjustment) !== 0 && (
                <div className="flex justify-between">
                  <span>端数調整</span>
                  <span>{yen(detail.rounding_adjustment, { sign: true })}</span>
                </div>
              )}
              <div className="flex justify-between text-xl font-bold">
                <span>請求額</span>
                <span>{yen(detail.total)}</span>
              </div>
              <div className="pt-2 border-t" style={{ borderColor: "var(--color-border)" }}>
                {detail.payments.map((p) => (
                  <div key={p.id} className="flex justify-between">
                    <span>
                      {p.method_label} {p.status === "refunded" && <span className="badge badge-danger">返金済</span>} {p.card_reference && `(${p.card_reference})`}
                    </span>
                    <span>
                      {yen(p.amount)}
                      {p.method === "cash" && p.tendered && ` (預り ${yen(p.tendered)} / 釣銭 ${yen(p.change_amount)})`}
                    </span>
                  </div>
                ))}
                {detail.refunds.map((r) => (
                  <div key={r.id} className="flex justify-between" style={{ color: "var(--color-warn)" }}>
                    <span>
                      返金（{r.method_label}）{r.reason} {r.method === "card" && (r.terminal_refund_done ? "・端末側返金済" : "・端末側の返金は未実施")}
                    </span>
                    <span>−{yen(r.amount)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Modal>
      )}

      {voidTarget && (
        <DeleteCheckoutDialog
          checkout={voidTarget}
          onClose={() => setVoidTarget(null)}
          onDeleted={async (mode) => {
            setSave({ kind: "saved", label: `会計#${voidTarget.number} を取り消しました（${mode === "refund" ? `${yen(voidTarget.total)} をお客様に返してください` : "伝票は会計前に戻りました"}）` });
            setVoidTarget(null);
            setDetail(null);
            await reloadAll();
          }}
        />
      )}
      {refundTarget && (
        <RefundDialog
          checkout={refundTarget}
          onClose={() => setRefundTarget(null)}
          onSubmit={async (body) => {
            setSave({ kind: "saving", label: "返金を記録中…" });
            try {
              await api(`/api/checkouts/${refundTarget.id}/refunds`, { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved", label: "返金を記録しました" });
              setRefundTarget(null);
              setDetail(null);
              await reloadAll();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}
      {collectTarget && (
        <CollectDialog
          receivable={collectTarget}
          onClose={() => setCollectTarget(null)}
          onSubmit={async (amount, method) => {
            setSave({ kind: "saving" });
            try {
              await api(`/api/receivables/${collectTarget.id}/collect`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { amount, method } });
              setSave({ kind: "saved", label: "回収を記録しました" });
              setCollectTarget(null);
              await reloadAll();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}
    </div>
  );
}

/** 会計の取消: 理由は聞かず、店舗の 4 桁の暗証番号を入れると記録ごと消す（伝票は会計前に戻る） */
function DeleteCheckoutDialog({ checkout, onClose, onDeleted }: { checkout: Checkout; onClose: () => void; onDeleted: (mode: "refund" | "redo") => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [resetKey, setResetKey] = useState(0);
  const [mode, setMode] = useState<"refund" | "redo" | null>(null);
  const hasCard = checkout.payments.some((p) => p.method === "card" && p.status === "valid");
  const code = err instanceof ApiError ? err.code : "";
  const locked = code === "void_pin_locked" || code === "void_pin_not_set";

  async function submit(pin: string) {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/checkouts/${checkout.id}/delete`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { pin, mode } });
      await onDeleted(mode ?? "redo");
    } catch (e) {
      setErr(e);
      setResetKey((k) => k + 1);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`会計#${checkout.number}（${yen(checkout.total)}）を取り消す`}
      onClose={busy ? undefined : onClose}
      footer={
        <button className="btn" onClick={onClose} disabled={busy}>
          やめる
        </button>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <button type="button" className={`btn btn-lg ${mode === "refund" ? "btn-primary" : ""}`} style={{ flexDirection: "column", alignItems: "flex-start", minHeight: 84 }} disabled={busy} onClick={() => setMode("refund")}>
            <span>お客様に全額返す</span>
            <span className="text-sm font-normal" style={{ opacity: 0.8 }}>伝票ごと取消。女の子のバックは給与に残ります</span>
          </button>
          <button type="button" className={`btn btn-lg ${mode === "redo" ? "btn-primary" : ""}`} style={{ flexDirection: "column", alignItems: "flex-start", minHeight: 84 }} disabled={busy} onClick={() => setMode("redo")}>
            <span>打ち間違い（会計し直す）</span>
            <span className="text-sm font-normal" style={{ opacity: 0.8 }}>伝票を会計前に戻します</span>
          </button>
        </div>
        <p className="text-sm">どちらも会計の記録は消え、売上・レジの計算からも消えます。理由の入力はありません。</p>
        {hasCard && (
          <p className="card-2 text-sm" style={{ borderColor: "var(--color-warn)" }}>
            カード払いがあります。決済端末（または決済会社の管理画面）でもカードの取消をしてください。
          </p>
        )}
        {mode && (
          <>
            <p className="font-semibold text-center">取消用の 4 桁の暗証番号を入れてください</p>
            <PinPad onComplete={submit} disabled={busy || locked} resetKey={resetKey} />
          </>
        )}
        {busy && <p className="text-center text-sm">取り消し中…</p>}
        {err ? (
          <p className="text-center font-semibold" role="alert" style={{ color: "var(--color-danger)" }}>
            {errorMessage(err)}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

function RefundDialog({ checkout, onClose, onSubmit }: { checkout: Checkout; onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<"cash" | "card">("cash");
  const [reason, setReason] = useState("");
  const [terminalDone, setTerminalDone] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <ConfirmDialog
      title={`会計#${checkout.number} の一部返金`}
      confirmLabel="返金を記録する"
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        setBusy(true);
        try {
          await onSubmit({ amount: toInt(amount), method, reason: reason.trim(), terminal_refund_done: terminalDone });
        } catch {
          /* 表示済み */
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-3">
        <p className="text-sm">会計は有効のまま、返金だけを元の会計に関連付けて記録します（現金返金は現金出納に反映されます）。</p>
        <div className="flex gap-2">
          <button className={`btn btn-sm ${method === "cash" ? "btn-primary" : ""}`} onClick={() => setMethod("cash")}>
            現金で返金
          </button>
          <button className={`btn btn-sm ${method === "card" ? "btn-primary" : ""}`} onClick={() => setMethod("card")}>
            カード（端末側で返金）
          </button>
        </div>
        {method === "card" && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={terminalDone} onChange={(e) => setTerminalDone(e.target.checked)} />
            決済端末側の返金を実施した（アプリ内の記録とは別操作です）
          </label>
        )}
        <Field label="返金額（円）">
          <input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
        <Field label="理由（必須）">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </ConfirmDialog>
  );
}

function CollectDialog({ receivable, onClose, onSubmit }: { receivable: Receivable; onClose: () => void; onSubmit: (amount: number, method: "cash" | "card") => Promise<void> }) {
  const [amount, setAmount] = useState(String(toInt(receivable.remaining)));
  const [method, setMethod] = useState<"cash" | "card">("cash");
  const [busy, setBusy] = useState(false);
  return (
    <ConfirmDialog
      title={`未収の回収（会計#${receivable.checkout_number}）`}
      confirmLabel="回収を記録する"
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        setBusy(true);
        try {
          await onSubmit(toInt(amount), method);
        } catch {
          /* 表示済み */
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-3">
        <p className="num">残額 {yen(receivable.remaining)}</p>
        <div className="flex gap-2">
          <button className={`btn btn-sm ${method === "cash" ? "btn-primary" : ""}`} onClick={() => setMethod("cash")}>
            現金
          </button>
          <button className={`btn btn-sm ${method === "card" ? "btn-primary" : ""}`} onClick={() => setMethod("card")}>
            カード
          </button>
        </div>
        <Field label="回収額（円）">
          <input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
      </div>
    </ConfirmDialog>
  );
}
