"use client";

/**
 * 経費・領収書管理（PC）: 確認待ちの承認・差戻し・却下、承認後の訂正、重複候補、レジ出金との突合、買掛金。
 * 左に原本、右に確定する内容。一括承認は警告の無いものだけ。
 */
import { useEffect, useMemo, useState } from "react";
import { FieldErrors, MonthPicker, ReasonPicker, RequireCaps, Tabs } from "@/components/common";
import { DataTable } from "@/components/DataTable";
import { FileViewer } from "@/components/FileViewer";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { monthRange, thisMonth } from "@/lib/accounting";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Account, AccountsResponse } from "@/lib/types_books";
import {
  DUPLICATE_REASON_LABELS, PAYMENT_METHODS, STATUS_BADGE, TAX_CATEGORY_LABELS, type AiSettings, type Payable, type PendingCounts, type ReceiptEvent, type ReceiptFull,
  type ReceiptList, type RegisterCheckRange,
} from "@/lib/types_expenses";
import { useLoad } from "@/lib/useLoad";

type TabKey = "pending" | "returned" | "approved" | "rejected" | "duplicates" | "register" | "payables" | "settings";
const TABS: { key: TabKey; label: string }[] = [
  { key: "pending", label: "確認待ち" },
  { key: "returned", label: "差戻し中" },
  { key: "approved", label: "承認済み" },
  { key: "rejected", label: "却下・取消" },
  { key: "duplicates", label: "重複候補" },
  { key: "register", label: "レジ出金との突合" },
  { key: "payables", label: "買掛金・未払金" },
  { key: "settings", label: "AI 読取の設定" },
];
const RETURN_REASONS = ["金額が読めません", "日付が違います", "支払先が分かりません", "店舗が違います", "領収書全体が写っていません"];
const REJECT_REASONS = ["事業と関係ない支出（私物）", "同じ領収書が登録済み", "領収書ではない", "別の方法で登録済み"];

export default function ExpensesAdminPage() {
  const [tab, setTab] = useState<TabKey>("pending");
  const counts = useLoad<PendingCounts>("/api/expenses/receipts/pending-count", [], { intervalMs: 30000 });
  const c = counts.data;
  const tabs = TABS.map((t) => ({
    ...t,
    badge: c ? ({ pending: c.pending + c.ocr_running, returned: c.returned, duplicates: c.duplicates } as Record<string, number>)[t.key] : undefined,
    tone: t.key === "duplicates" ? ("danger" as const) : undefined,
  }));
  return (
    <RequireCaps anyCaps={["expenses.approve", "accounting.view"]}>
      <PageHeader title="経費・領収書管理" subtitle="店舗で撮った領収書を確認して承認します。承認すると帳簿に入ります（店舗現金はレジの現金経費、それ以外は経費として）。" />
      <Tabs tabs={tabs} value={tab} onChange={(k) => setTab(k as TabKey)} size="sm" />
      {tab === "register" ? <RegisterCheck /> : tab === "payables" ? <Payables /> : tab === "settings" ? <AiSettingsPanel /> : <ReceiptsTab tab={tab} onChanged={() => counts.reload()} />}
    </RequireCaps>
  );
}

function statusesFor(tab: TabKey): string {
  const map: Partial<Record<TabKey, string>> = { pending: "pending,ocr_running", returned: "returned", approved: "approved", rejected: "rejected,voided", duplicates: "pending,returned" };
  return map[tab] ?? "pending";
}

function ReceiptsTab({ tab, onChanged }: { tab: TabKey; onChanged: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [active, setActive] = useState<number | null>(null);
  const extra = tab === "duplicates" ? "&duplicates=1" : "";
  const q = useLoad<ReceiptList<ReceiptFull>>(`/api/expenses/receipts?status=${statusesFor(tab)}${extra}&page=${page}&page_size=50`, [tab, page]);
  const accounts = useLoad<AccountsResponse>("/api/accounting/accounts?active=1");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErr, setBulkErr] = useState<string | null>(null);

  useEffect(() => {
    setSelected(new Set());
    setActive(null);
    setPage(1);
  }, [tab]);

  const rows = q.data?.items ?? [];
  const canApprove = can("expenses.approve");
  const bulkTargets = rows.filter((r) => selected.has(r.id) && r.status === "pending" && !r.duplicate_candidates.length && !r.ocr.warnings.length && r.payment_method !== "other" && r.total_amount);

  async function bulkApprove() {
    setBulkBusy(true);
    setBulkErr(null);
    let ok = 0;
    const errors: string[] = [];
    for (const r of bulkTargets) {
      const key = r.ocr.suggested_account_key || "misc_expense";
      try {
        await api(`/api/expenses/receipts/${r.id}/approve`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { account: key, version: r.version } });
        ok += 1;
      } catch (e) {
        errors.push(`#${r.id}: ${errorMessage(e)}`);
      }
    }
    setBulkBusy(false);
    toast.show({ message: `${ok} 件を承認しました${errors.length ? `（${errors.length} 件は要確認）` : ""}`, tone: errors.length ? "info" : "ok" });
    if (errors.length) setBulkErr(errors.join(" / "));
    setSelected(new Set());
    await q.reload();
    onChanged();
  }

  return (
    <div className="space-y-3">
      {canApprove && tab === "pending" && (
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn btn-sm btn-ok" disabled={bulkBusy || bulkTargets.length === 0} onClick={bulkApprove}>
            選んだ {bulkTargets.length} 件を一括承認（警告なし・AI の提案科目で）
          </button>
          <span className="text-sm" style={{ color: "var(--color-muted)" }}>
            重複候補・読取の注意・支払方法「その他」のものは一括できません。1 件ずつ確認してください。
          </span>
        </div>
      )}
      {bulkErr && <p className="badge badge-warn">{bulkErr}</p>}
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner />}
      {q.data && (
        <div className="grid xl:grid-cols-[1fr_1.1fr] gap-4">
          <DataTable<ReceiptFull>
            columns={[
              { key: "status", label: "状態", render: (r) => <span className={STATUS_BADGE[r.status]}>{r.status_label}</span>, width: 110 },
              { key: "receipt_date", label: "日付", render: (r) => (r.receipt_date ? fmtDate(r.receipt_date) : "—"), width: 100 },
              { key: "store_name", label: "店舗" },
              { key: "vendor", label: "支払先", render: (r) => r.approved_vendor || r.vendor || "—" },
              { key: "total_amount", label: "金額", align: "right", render: (r) => yen(r.approved_amount ?? r.total_amount) },
              { key: "payment_method", label: "支払", render: (r) => PAYMENT_METHODS.find((m) => m.key === (r.approved_payment_method || r.payment_method))?.label },
              {
                key: "flags",
                label: "",
                render: (r) => (
                  <span className="flex gap-1">
                    {r.duplicate_candidates.length > 0 && <span className="badge badge-danger">重複?</span>}
                    {r.ocr.warnings.length > 0 && <span className="badge badge-warn">注意</span>}
                    {(r.ocr.status === "failed" || r.ocr.status === "skipped") && <span className="badge">手入力</span>}
                  </span>
                ),
              },
            ]}
            rows={rows}
            rowKey={(r) => r.id}
            selectable={canApprove && tab === "pending"}
            selected={selected}
            onSelectedChange={setSelected}
            onRowClick={(r) => setActive(r.id)}
            activeKey={active}
            page={page}
            pageSize={50}
            total={q.data.total}
            onPageChange={setPage}
            keyboard
            emptyText="該当する領収書はありません。"
          />
          <div>
            {active && accounts.data ? (
              <ReceiptDetail
                key={active}
                id={active}
                accounts={accounts.data.accounts}
                onChanged={async () => {
                  await q.reload();
                  onChanged();
                }}
                onClose={() => setActive(null)}
              />
            ) : (
              <Empty>左の一覧から領収書を選ぶと、原本と内容がここに出ます。{canApprove ? "キーボード: j / k で移動、Enter で開く。" : ""}</Empty>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ReceiptDetail({ id, accounts, onChanged, onClose }: { id: number; accounts: Account[]; onChanged: () => Promise<void>; onClose: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const q = useLoad<ReceiptFull>(`/api/expenses/receipts/${id}`, [id]);
  const events = useLoad<ReceiptEvent[]>(`/api/expenses/receipts/${id}/events`, [id]);
  const r = q.data;
  const [form, setForm] = useState<{ date: string; vendor: string; amount: string; account: string; tax_category: string; store: string; payment_method: string; note: string; override: string; cash_movement_id: string; closed_day_mode: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<"return" | "reject" | "correct" | null>(null);
  const [reason, setReason] = useState("");
  const expenseAccounts = useMemo(() => accounts.filter((a) => (a.category === "expense" || a.category === "cogs") && a.is_active), [accounts]);

  useEffect(() => {
    if (r && !form) {
      const suggested = r.ocr.suggested_account_key || r.account_key || "";
      setForm({
        date: r.approved_date ?? r.receipt_date ?? r.ocr.date ?? "",
        vendor: r.approved_vendor || r.vendor || r.ocr.vendor || "",
        amount: String(Math.round(Number(r.approved_amount ?? r.total_amount ?? r.ocr.total ?? 0)) || ""),
        account: suggested,
        tax_category: r.approved_tax_category || expenseAccounts.find((a) => a.key === suggested)?.default_tax || "purchase_10",
        store: String(r.approved_store ?? r.store),
        payment_method: r.approved_payment_method || (r.payment_method === "other" ? "" : r.payment_method),
        note: r.approved_note || "",
        override: r.duplicate_override_reason || "",
        cash_movement_id: "",
        closed_day_mode: "",
      });
    }
  }, [r, form, expenseAccounts]);

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!r || !form) return null;
  const canApprove = can("expenses.approve");
  const pending = r.status === "pending";

  async function act(action: "approve" | "return" | "reject" | "correct", body: Record<string, unknown>) {
    if (!r) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/expenses/receipts/${r.id}/${action}`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { ...body, version: r.version } });
      toast.show({ message: { approve: "承認しました（帳簿に入ります）", return: "差し戻しました", reject: "却下しました", correct: "訂正しました" }[action] });
      setDialog(null);
      setReason("");
      await q.reload();
      await onChanged();
    } catch (e) {
      setErr(e);
      if (e instanceof ApiError && (e.code === "business_day_closed" || e.code === "business_day_missing")) {
        // 承認者に選ばせる（レジ以外の手許現金払いとして登録）
        setForm((f) => (f ? { ...f, closed_day_mode: "office_cash" } : f));
      }
    } finally {
      setBusy(false);
    }
  }

  const approveBody = () => ({
    date: form.date,
    vendor: form.vendor,
    amount: form.amount,
    account: form.account,
    tax_category: form.tax_category,
    store: form.store,
    payment_method: form.payment_method,
    note: form.note,
    duplicate_override_reason: form.override,
    cash_movement_id: form.cash_movement_id || undefined,
    closed_day_mode: form.closed_day_mode || undefined,
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">
          領収書 #{r.id} <span className={STATUS_BADGE[r.status]}>{r.status_label}</span>
        </h2>
        <button className="btn btn-sm btn-ghost" onClick={onClose}>
          閉じる
        </button>
      </div>
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        {r.store_name} ・ 登録 {r.submitted_by_name} {fmtDateTime(r.submitted_at ?? r.created_at)} ・ 読取: {r.ocr.status_label}
        {r.ocr.model ? `（${r.ocr.model}）` : ""}
      </div>
      <ErrorBox error={err} />
      {err instanceof ApiError && err.details && err.code !== "duplicate_suspected" ? <FieldErrors details={err.details} /> : null}
      {err instanceof ApiError && (err.code === "business_day_closed" || err.code === "business_day_missing") && (
        <p className="badge badge-warn" style={{ whiteSpace: "normal" }}>
          「レジ以外の手許現金払い」として登録する設定にしました。もう一度「承認」を押すと経費として登録します。レジの出金として入れたい場合は、開店・日締め画面でその営業日を再開してください。
        </p>
      )}
      <div className="grid lg:grid-cols-2 gap-3">
        <FileViewer path={`/api/expenses/receipts/${r.id}/file?inline=1`} name={r.file_name} height={460} />
        <div className="space-y-2">
          {r.ocr.warnings.map((w) => (
            <p key={w.code} className="badge badge-warn" style={{ whiteSpace: "normal" }}>
              {w.message}
            </p>
          ))}
          {r.ocr.missing_fields.length > 0 && <p className="badge">読めなかった項目: {r.ocr.missing_fields.join("・")}</p>}
          {r.quality_flags.includes("low_resolution") && <p className="badge badge-warn">画像が小さめです（紙の原本も保管してください）</p>}
          {r.duplicate_candidates.length > 0 && (
            <div className="card-2" style={{ borderColor: "var(--color-danger)" }}>
              <div className="font-bold" style={{ color: "var(--color-danger)" }}>
                同じ領収書の可能性があります
              </div>
              <ul className="text-sm">
                {r.duplicate_candidates.map((c) => (
                  <li key={c.receipt_id}>
                    #{c.receipt_id}（{c.date ? fmtDate(c.date) : "日付なし"} {c.vendor} {c.amount ? yen(c.amount) : ""}・{c.status}）: {c.reasons.map((x) => DUPLICATE_REASON_LABELS[x] ?? x).join("、")}
                  </li>
                ))}
              </ul>
              {pending && canApprove && (
                <Field label="別の取引として承認する理由（必須）" hint="同じなら「却下」してください">
                  <input className="input" value={form.override} onChange={(e) => setForm({ ...form, override: e.target.value })} />
                </Field>
              )}
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Field label="日付">
              <input type="date" className="input" value={form.date} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, date: e.target.value })} />
            </Field>
            <Field label="金額（税込）">
              <input className="input num" inputMode="numeric" value={form.amount} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(/[^\d]/g, "") })} />
            </Field>
          </div>
          <Field label="支払先">
            <input className="input" value={form.vendor} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
          </Field>
          {(r.tax_breakdown.length > 0 || r.approved_tax_breakdown.length > 0) && (
            <div className="text-sm" style={{ color: "var(--color-muted)" }}>
              内訳: {(r.approved_tax_breakdown.length ? r.approved_tax_breakdown : r.tax_breakdown).map((b) => `${b.rate}% ${yen(b.taxable_incl)}`).join(" / ")}
              {r.invoice_number ? ` ・ 登録番号 ${r.invoice_number}` : ""}
            </div>
          )}
          <Field label="勘定科目" hint={r.ocr.suggested_accounts?.length ? `AI の提案: ${r.ocr.suggested_accounts.map((s) => `${accounts.find((a) => a.key === s.key)?.name ?? s.key}（${s.reason}）`).join("、")}` : undefined}>
            <select
              className="input"
              value={form.account}
              disabled={!pending && dialog !== "correct"}
              onChange={(e) => {
                const acc = expenseAccounts.find((a) => a.key === e.target.value);
                setForm({ ...form, account: e.target.value, tax_category: acc?.default_tax ?? form.tax_category });
              }}
            >
              <option value="">選んでください</option>
              {expenseAccounts.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="税区分">
              <select className="input" value={form.tax_category} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, tax_category: e.target.value })}>
                {Object.entries(TAX_CATEGORY_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="支払方法">
              <select className="input" value={form.payment_method} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, payment_method: e.target.value, closed_day_mode: "" })}>
                <option value="">選んでください</option>
                {PAYMENT_METHODS.filter((m) => m.key !== "other").map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {form.payment_method === "store_cash" && pending && (r.cash_movement_candidates?.length ?? 0) > 0 && (
            <Field label="このレジ出金の領収書ですか？（結び付けると新しい出金は作りません）">
              <select className="input" value={form.cash_movement_id} onChange={(e) => setForm({ ...form, cash_movement_id: e.target.value })}>
                <option value="">新しくレジ出金を作る</option>
                {r.cash_movement_candidates!.map((m) => (
                  <option key={m.cash_movement_id} value={String(m.cash_movement_id)}>
                    {yen(m.amount)} {m.reason}（{fmtDateTime(m.occurred_at)}）{m.amount_match ? " ← 金額一致" : ""}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {form.closed_day_mode === "office_cash" && <p className="badge badge-warn">レジ以外の手許現金払いの経費として登録します</p>}
          <Field label="承認メモ（任意）">
            <input className="input" value={form.note} disabled={!pending && dialog !== "correct"} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
          {r.status === "approved" && (
            <div className="card-2 text-sm">
              承認: {r.reviewed_by_name} {fmtDateTime(r.reviewed_at)} ・ 科目 {r.account_name} ・ {TAX_CATEGORY_LABELS[r.approved_tax_category] ?? r.approved_tax_category} ・ 店舗 {r.approved_store_name}
              {r.cash_movement ? " ・ レジ出金に反映" : r.expense ? " ・ 経費として登録" : r.payable ? " ・ 買掛金として登録" : ""}
            </div>
          )}
          {canApprove && pending && (
            <div className="grid grid-cols-3 gap-2 pt-2">
              <button className="btn btn-lg btn-ok" disabled={busy || !form.account || !form.payment_method || !form.amount} onClick={() => act("approve", approveBody())}>
                承認
              </button>
              <button className="btn btn-lg" disabled={busy} onClick={() => setDialog("return")}>
                差し戻す
              </button>
              <button className="btn btn-lg btn-danger" disabled={busy} onClick={() => setDialog("reject")}>
                却下
              </button>
            </div>
          )}
          {canApprove && r.status === "returned" && (
            <button className="btn btn-danger" disabled={busy} onClick={() => setDialog("reject")}>
              却下する
            </button>
          )}
          {canApprove && r.status === "approved" && dialog !== "correct" && (
            <button className="btn" disabled={busy} onClick={() => setDialog("correct")}>
              承認内容を訂正する（取消＋再登録）
            </button>
          )}
          {dialog === "correct" && (
            <div className="card-2 space-y-2" style={{ borderColor: "var(--color-warn)" }}>
              <p className="font-semibold">上の項目を直して、理由を入れて「訂正を確定」。元の記録は取消として残ります。</p>
              <input className="input" placeholder="訂正の理由（必須）" value={reason} onChange={(e) => setReason(e.target.value)} />
              <div className="flex gap-2">
                <button className="btn btn-primary" disabled={busy || !reason} onClick={() => act("correct", { ...approveBody(), reason })}>
                  訂正を確定
                </button>
                <button className="btn" onClick={() => setDialog(null)}>
                  やめる
                </button>
              </div>
            </div>
          )}
          <details className="text-sm">
            <summary style={{ color: "var(--color-muted)" }}>履歴</summary>
            <ul className="mt-1 space-y-1">
              {(events.data ?? []).map((e) => (
                <li key={e.id}>
                  {fmtDateTime(e.at)} {e.action} {e.actor_name}
                  {e.reason ? ` — ${e.reason}` : ""}
                </li>
              ))}
            </ul>
          </details>
        </div>
      </div>
      {(dialog === "return" || dialog === "reject") && (
        <Modal
          title={dialog === "return" ? "差し戻す理由" : "却下する理由"}
          onClose={busy ? undefined : () => setDialog(null)}
          footer={
            <>
              <button className="btn" onClick={() => setDialog(null)} disabled={busy}>
                やめる
              </button>
              <button className={`btn ${dialog === "reject" ? "btn-danger" : "btn-primary"}`} disabled={busy || !reason} onClick={() => act(dialog, { reason })}>
                {dialog === "return" ? "差し戻す" : "却下する"}
              </button>
            </>
          }
        >
          <ReasonPicker options={dialog === "return" ? RETURN_REASONS : REJECT_REASONS} value={reason} onChange={setReason} />
        </Modal>
      )}
    </div>
  );
}

function RegisterCheck() {
  const { store } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const { from, to } = monthRange(month);
  const q = useLoad<RegisterCheckRange>(store ? `/api/expenses/register-check?from=${from}&to=${to}` : null, [from, to]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <MonthPicker value={month} onChange={setMonth} />
        {q.data && (
          <>
            <Stat label="領収書なしの現金経費" value={`${q.data.no_receipt_count} 件`} accent={q.data.no_receipt_count ? "warn" : "ok"} />
            <Stat label="合わない日" value={`${q.data.diff_days} 日`} accent={q.data.diff_days ? "warn" : "ok"} />
          </>
        )}
      </div>
      <ErrorBox error={q.error} />
      {q.loading && <Spinner />}
      {q.data && q.data.rows.length === 0 && <Empty>この月の現金経費はありません。</Empty>}
      {q.data?.rows.map((d) => (
        <div key={d.date} className="card-2">
          <div className="flex flex-wrap items-center gap-3">
            <strong>{fmtDate(d.date)}</strong>
            <span className={`badge ${d.status === "match" ? "badge-ok" : d.status === "diff" ? "badge-warn" : ""}`}>{d.status === "match" ? "一致" : d.status === "diff" ? "差あり" : "データなし"}</span>
            <span className="num">レジの現金経費 {yen(d.pos_expense_total)}</span>
            <span className="num">領収書 {yen(d.receipt_total)}</span>
            <span className="num" style={{ color: d.difference !== "0" && d.difference !== "0.00" ? "var(--color-warn)" : undefined }}>
              差 {yen(d.difference)}
            </span>
          </div>
          {d.no_receipt.length > 0 && (
            <ul className="text-sm mt-1">
              {d.no_receipt.map((m) => (
                <li key={m.cash_movement_id}>
                  領収書なし: {yen(m.amount)} {m.reason}（{fmtDateTime(m.occurred_at)}）
                </li>
              ))}
            </ul>
          )}
          {d.receipt_without_movement.length > 0 && (
            <ul className="text-sm mt-1" style={{ color: "var(--color-warn)" }}>
              {d.receipt_without_movement.map((r) => (
                <li key={r.receipt_id}>
                  出金に結び付いていない領収書 #{r.receipt_id}: {yen(r.amount)} {r.vendor}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

function Payables() {
  const { can } = useSession();
  const toast = useToast();
  const [month, setMonth] = useState(thisMonth());
  const { from, to } = monthRange(month);
  const [showOpen, setShowOpen] = useState(true);
  const q = useLoad<{ items: Payable[]; total: number }>(`/api/expenses/payables?${showOpen ? "status=open,partially_paid" : `from=${from}&to=${to}`}`, [showOpen, from, to]);
  const accounts = useLoad<AccountsResponse>("/api/accounting/accounts?active=1");
  const [create, setCreate] = useState(false);
  const [pay, setPay] = useState<Payable | null>(null);
  const [voidTarget, setVoidTarget] = useState<Payable | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const openTotal = (q.data?.items ?? []).filter((p) => p.status !== "paid" && !p.is_voided).reduce((s, p) => s + Number(p.balance), 0);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button className={`btn btn-sm ${showOpen ? "btn-primary" : ""}`} onClick={() => setShowOpen(true)}>
          未払のもの
        </button>
        <button className={`btn btn-sm ${!showOpen ? "btn-primary" : ""}`} onClick={() => setShowOpen(false)}>
          月で見る
        </button>
        {!showOpen && <MonthPicker value={month} onChange={setMonth} />}
        <Stat label="未払の残高" value={yen(openTotal)} accent={openTotal > 0 ? "warn" : "ok"} />
        {can("accounting.bookkeep") && (
          <button className="btn btn-sm ml-auto" onClick={() => setCreate(true)}>
            ＋ 請求書・未払を登録
          </button>
        )}
      </div>
      <ErrorBox error={err} />
      <ErrorBox error={q.error} />
      {q.loading && <Spinner />}
      {q.data && (
        <DataTable<Payable>
          columns={[
            { key: "invoice_date", label: "請求日", render: (p) => fmtDate(p.invoice_date) },
            { key: "due_date", label: "支払期日", render: (p) => (p.due_date ? fmtDate(p.due_date) : "—") },
            { key: "counterparty", label: "取引先" },
            { key: "account_name", label: "科目" },
            { key: "description", label: "内容" },
            { key: "amount", label: "金額", align: "right", render: (p) => yen(p.amount) },
            { key: "paid_amount", label: "支払済", align: "right", render: (p) => yen(p.paid_amount) },
            { key: "balance", label: "残高", align: "right", render: (p) => yen(p.balance) },
            { key: "status", label: "状態", render: (p) => <span className={`badge ${p.status === "paid" ? "badge-ok" : p.is_voided ? "" : "badge-warn"}`}>{{ open: "未払", partially_paid: "一部支払", paid: "支払済", voided: "取消" }[p.status]}</span> },
            {
              key: "actions",
              label: "",
              render: (p) =>
                can("accounting.bookkeep") && !p.is_voided ? (
                  <span className="flex gap-1">
                    {p.status !== "paid" && (
                      <button className="btn btn-sm" onClick={() => setPay(p)}>
                        支払いを登録
                      </button>
                    )}
                    {Number(p.paid_amount) === 0 && (
                      <button className="btn btn-sm btn-ghost" onClick={() => setVoidTarget(p)}>
                        取消
                      </button>
                    )}
                  </span>
                ) : null,
            },
          ]}
          rows={q.data.items}
          rowKey={(p) => p.id}
          emptyText="買掛金・未払金はありません。"
        />
      )}
      {create && accounts.data && (
        <PayableModal
          accounts={accounts.data.accounts.filter((a) => (a.category === "expense" || a.category === "cogs") && a.is_active)}
          onClose={() => setCreate(false)}
          onSaved={async () => {
            setCreate(false);
            toast.show({ message: "登録しました" });
            await q.reload();
          }}
        />
      )}
      {pay && (
        <PaymentModal
          payable={pay}
          onClose={() => setPay(null)}
          onSaved={async () => {
            setPay(null);
            toast.show({ message: "支払いを登録しました" });
            await q.reload();
          }}
        />
      )}
      {voidTarget && (
        <ConfirmDialog
          title="この買掛金を取り消しますか"
          message={`${voidTarget.counterparty} ${yen(voidTarget.amount)} を取り消します（履歴は残ります）。`}
          confirmLabel="取り消す"
          danger
          onCancel={() => setVoidTarget(null)}
          onConfirm={async () => {
            try {
              await api(`/api/expenses/payables/${voidTarget.id}/void`, { method: "POST", body: { reason: "誤登録" } });
              setVoidTarget(null);
              await q.reload();
            } catch (e) {
              setErr(e);
              setVoidTarget(null);
            }
          }}
        />
      )}
    </div>
  );
}

function PayableModal({ accounts, onClose, onSaved }: { accounts: Account[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [f, setF] = useState({ counterparty: "", invoice_date: new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }), due_date: "", amount: "", account: "purchases", description: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="請求書・未払の登録"
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !f.amount || !f.counterparty}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await api("/api/expenses/payables", { method: "POST", idempotencyKey: newIdempotencyKey(), body: f });
                await onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            登録
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="取引先">
          <input className="input" value={f.counterparty} onChange={(e) => setF({ ...f, counterparty: e.target.value })} />
        </Field>
        <Field label="金額（税込）">
          <input className="input num" inputMode="numeric" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^\d]/g, "") })} />
        </Field>
        <Field label="請求日・取引日">
          <input type="date" className="input" value={f.invoice_date} onChange={(e) => setF({ ...f, invoice_date: e.target.value })} />
        </Field>
        <Field label="支払期日（任意）">
          <input type="date" className="input" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} />
        </Field>
        <Field label="科目">
          <select className="input" value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })}>
            {accounts.map((a) => (
              <option key={a.key} value={a.key}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="内容">
          <input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </Field>
      </div>
      <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
        仕入（売上原価）の科目は買掛金、それ以外は未払金として帳簿に入ります。
      </p>
    </Modal>
  );
}

function PaymentModal({ payable, onClose, onSaved }: { payable: Payable; onClose: () => void; onSaved: () => Promise<void> }) {
  const [f, setF] = useState({ date: new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" }), amount: String(Math.round(Number(payable.balance))), method: "bank", note: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`支払いの登録: ${payable.counterparty}（残高 ${yen(payable.balance)}）`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !f.amount}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await api(`/api/expenses/payables/${payable.id}/payments`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: f });
                await onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            登録
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="grid grid-cols-2 gap-2">
        <Field label="支払日">
          <input type="date" className="input" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />
        </Field>
        <Field label="金額">
          <input className="input num" inputMode="numeric" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^\d]/g, "") })} />
        </Field>
      </div>
      <Field label="支払方法">
        <div className="grid grid-cols-2 gap-2">
          {[
            ["bank", "事業用口座から"],
            ["cash_register", "この店舗のレジ現金から"],
            ["owner_personal", "オーナー個人のお金"],
            ["card", "事業用カード（未払金へ）"],
          ].map(([k, label]) => (
            <button key={k} type="button" className={`btn ${f.method === k ? "btn-primary" : ""}`} onClick={() => setF({ ...f, method: k })}>
              {label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="メモ">
        <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      </Field>
    </Modal>
  );
}

function AiSettingsPanel() {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<AiSettings>("/api/expenses/settings/ai");
  const [err, setErr] = useState<unknown>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const s = q.data;
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!s) return null;
  async function save(body: Record<string, unknown>) {
    setErr(null);
    try {
      await withReauth(() => api("/api/expenses/settings/ai", { method: "PUT", body }));
      toast.show({ message: "保存しました" });
      await q.reload();
    } catch (e) {
      setErr(e);
    }
  }
  return (
    <div className="card space-y-3 max-w-2xl">
      <ErrorBox error={err} />
      <div className="grid grid-cols-2 gap-3">
        <Stat label="AI 読取" value={s.available ? "使えます" : "使えません"} accent={s.available ? "ok" : "warn"} sub={s.reason || `接続先: ${s.provider}${s.model ? ` / ${s.model}` : ""}`} />
        <Stat label="今月の使用額（概算）" value={yen(s.spent_jpy)} sub={`予算 ${Number(s.budget_jpy) ? yen(s.budget_jpy) : "上限なし"}`} />
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        AI は領収書の読取を手伝うだけです。金額・科目・税区分は必ず人が確認してから承認します。送るのは縮小した画像だけで、店舗名・氏名・ファイル名は送りません。
        サーバー側の接続先: {s.server_provider}
      </p>
      {can("accounting.settings") && (
        <div className="space-y-2">
          <button className={`btn ${s.enabled ? "btn-ok" : ""}`} onClick={() => save({ ai_enabled: !s.enabled })}>
            {s.enabled ? "AI 読取: オン（押すとオフ）" : "AI 読取: オフ（押すとオン）"}
          </button>
          <Field label="月の予算（円。0 で上限なし）">
            <div className="flex gap-2">
              <input className="input num" style={{ maxWidth: 200 }} inputMode="numeric" value={budget ?? String(Math.round(Number(s.budget_jpy)))} onChange={(e) => setBudget(e.target.value.replace(/[^\d]/g, ""))} />
              <button className="btn" onClick={() => save({ ai_monthly_budget_jpy: budget ?? s.budget_jpy })}>
                保存
              </button>
            </div>
          </Field>
        </div>
      )}
    </div>
  );
}
