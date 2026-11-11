"use client";

import { useState } from "react";
import { Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, downloadFile, newIdempotencyKey, uploadFile } from "@/lib/api";
import { EXPENSE_ACCOUNTS, monthRange, thisMonth, todayJst } from "@/lib/accounting";
import { fmtDate, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Journal } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Tab = "pl" | "reconcile" | "journals" | "expenses" | "evidence" | "owner" | "card";
const TABS: { key: Tab; label: string }[] = [
  { key: "pl", label: "月次損益" },
  { key: "reconcile", label: "照合" },
  { key: "journals", label: "仕訳" },
  { key: "expenses", label: "経費" },
  { key: "evidence", label: "証憑（領収書など）" },
  { key: "owner", label: "経営者との資金移動" },
  { key: "card", label: "カード入金" },
];

export default function BooksPage() {
  const [tab, setTab] = useState<Tab>("pl");
  const [month, setMonth] = useState(thisMonth());
  const { from, to } = monthRange(month);
  return (
    <div className="space-y-4">
      <PageHeader
        title="月次損益・経費・証憑"
        subtitle="POS の売上・レジ現金・日払い・経費から仕訳を作り、実際の記録に基づいて損益を集計します。売上や経費の削除・修正は取消と再登録で履歴を残します。"
        actions={<input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />}
      />
      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button key={t.key} className={`btn ${tab === t.key ? "btn-primary" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "pl" && <ProfitLoss from={from} to={to} />}
      {tab === "reconcile" && <Reconcile from={from} to={to} />}
      {tab === "journals" && <Journals from={from} to={to} />}
      {tab === "expenses" && <Expenses from={from} to={to} />}
      {tab === "evidence" && <Evidences />}
      {tab === "owner" && <OwnerTransfers from={from} to={to} />}
      {tab === "card" && <CardSettlements from={from} to={to} />}
    </div>
  );
}

type PL = { income: { key: string; name: string; amount: string }[]; expense: { key: string; name: string; amount: string }[]; total_income: string; total_expense: string; profit: string; suspense_total: string; notice: string };

function BuildButton({ from, to, onBuilt }: { from: string; to: string; onBuilt: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!can("accounting.bookkeep")) return null;
  return (
    <span>
      <button
        className="btn btn-sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const r = await api<{ built: number }>("/api/accounting/journals/build", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { from, to } });
            toast.show({ message: `仕訳を作成・更新しました（${r.built}件）` });
            onBuilt();
          } catch (e) {
            setErr(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "作成中…" : "最新の記録から仕訳を作る"}
      </button>
      <ErrorBox error={err} />
    </span>
  );
}

function ProfitLoss({ from, to }: { from: string; to: string }) {
  const q = useLoad<PL>(`/api/accounting/pl?from=${from}&to=${to}`);
  if (q.loading) return <Spinner />;
  const d = q.data;
  return (
    <div className="space-y-3">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      <BuildButton from={from} to={to} onBuilt={() => q.reload()} />
      {d && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Stat label="収益" value={yen(d.total_income)} />
            <Stat label="費用" value={yen(d.total_expense)} />
            <Stat label={toInt(d.profit) >= 0 ? "利益（黒字）" : "損失（赤字）"} value={yen(d.profit)} accent={toInt(d.profit) >= 0 ? "ok" : "danger"} />
          </div>
          {toInt(d.suspense_total) > 0 && (
            <p className="font-semibold" style={{ color: "var(--color-warn)" }}>
              仮勘定（内容不明の入出金）が {yen(d.suspense_total)} あります。勘定科目を確認するまで損益は確定しません（「仕訳」タブの注意を確認）。
            </p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[
              { title: "収益", rows: d.income },
              { title: "費用", rows: d.expense },
            ].map((g) => (
              <div key={g.title} className="card">
                <h3 className="font-bold mb-2">{g.title}</h3>
                {g.rows.length === 0 ? (
                  <Empty>なし</Empty>
                ) : (
                  <table className="table">
                    <tbody>
                      {g.rows.map((r) => (
                        <tr key={r.key}>
                          <td>{r.name}</td>
                          <td className="text-right num">{yen(r.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}
          </div>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            {d.notice}
          </p>
        </>
      )}
    </div>
  );
}

type Recon = { checks: { code: string; label: string; left: string; right: string; ok: boolean; note: string }[]; freee_sync: Record<string, number>; journal_count: number };

function Reconcile({ from, to }: { from: string; to: string }) {
  const q = useLoad<Recon>(`/api/accounting/reconcile?from=${from}&to=${to}`);
  if (q.loading) return <Spinner />;
  return (
    <div className="space-y-3">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      <BuildButton from={from} to={to} onBuilt={() => q.reload()} />
      {q.data?.checks.map((c) => (
        <div key={c.code} className="card-2 flex flex-wrap items-center gap-3" style={{ borderColor: c.ok ? "var(--color-ok)" : "var(--color-warn)" }}>
          <span className={c.ok ? "badge badge-ok" : "badge badge-warn"}>{c.ok ? "一致" : "要確認"}</span>
          <span className="font-semibold">{c.label}</span>
          <span className="num">
            {yen(c.left)} ／ {yen(c.right)}
          </span>
          {c.note && <span className="text-sm" style={{ color: "var(--color-muted)" }}>{c.note}</span>}
        </div>
      ))}
      {q.data && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          仕訳 {q.data.journal_count}件 ・ freee 連携: {Object.entries(q.data.freee_sync).map(([k, n]) => `${k} ${n}`).join("、") || "なし"}
        </p>
      )}
    </div>
  );
}

function Journals({ from, to }: { from: string; to: string }) {
  const q = useLoad<{ journals: Journal[]; accounts: Record<string, string>; taxes: Record<string, string> }>(`/api/accounting/journals?from=${from}&to=${to}`);
  const [err, setErr] = useState<unknown>(null);
  if (q.loading) return <Spinner />;
  const d = q.data;
  return (
    <div className="space-y-3">
      <ErrorBox error={q.error || err} onRetry={() => q.reload(false)} />
      <div className="flex flex-wrap gap-2 items-center">
        <BuildButton from={from} to={to} onBuilt={() => q.reload()} />
        <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/journals.csv?from=${from}&to=${to}`).catch(setErr)}>
          仕訳CSV（借方・貸方の列）
        </button>
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          freee の「他社会計ソフト仕訳のインポート」で列を割り当てて取り込めます（freee 公式テンプレートと同じ並びであることは保証しません）。
        </span>
      </div>
      {d && d.journals.length === 0 && <Empty>この期間の仕訳はまだありません。「最新の記録から仕訳を作る」を押してください（締め済みの営業日だけ仕訳になります）。</Empty>}
      {d?.journals.map((j) => (
        <div key={j.id} className="card-2 space-y-1" style={{ opacity: j.is_void ? 0.5 : 1 }}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="num">{fmtDate(j.issue_date)}</span>
            <span className="font-semibold">{j.description}</span>
            {j.revision > 1 && <span className="badge">修正 {j.revision - 1}回</span>}
            {j.is_void && <span className="badge">取消</span>}
            <span className="badge ml-auto">freee: {j.sync_status_label}</span>
          </div>
          <table className="table text-sm">
            <tbody>
              {j.lines.map((l, i) => (
                <tr key={i}>
                  <td style={{ width: 60 }}>{l.side === "debit" ? "借方" : "貸方"}</td>
                  <td>{d.accounts[l.key] ?? l.key}</td>
                  <td>{d.taxes[l.tax] ?? ""}</td>
                  <td className="text-right num">{yen(l.amount)}</td>
                  <td>{l.memo}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {j.warnings.map((w) => (
            <p key={w} className="text-sm" style={{ color: "var(--color-warn)" }}>
              ⚠ {w}
            </p>
          ))}
        </div>
      ))}
    </div>
  );
}

type ExpenseRow = { id: number; date: string; amount: string; tax_rate: string; account_key: string; counterparty: string; invoice_number: string; description: string; paid_from: string; paid_from_label: string; evidence: number | null; is_voided: boolean; void_reason: string; corrected_from: number | null };

function Expenses({ from, to }: { from: string; to: string }) {
  const { can } = useSession();
  const q = useLoad<ExpenseRow[]>(`/api/accounting/expenses?from=${from}&to=${to}`);
  const [open, setOpen] = useState(false);
  const [voiding, setVoiding] = useState<ExpenseRow | null>(null);
  const accountLabel = (k: string) => EXPENSE_ACCOUNTS.find((a) => a.key === k)?.label ?? k;
  return (
    <div className="space-y-3">
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      <div className="flex items-center gap-2">
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          レジ現金で払った経費は「開店・日締め・現金」で記録済みです。ここには口座・カード・個人のお金で払った経費を登録します。経営者の個人のお金で払った分は、個人事業主は事業主借、法人は役員借入金として仕訳します。
        </p>
        {can("accounting.bookkeep") && (
          <button className="btn btn-primary ml-auto" onClick={() => setOpen(true)}>
            ＋ 経費を登録
          </button>
        )}
      </div>
      {q.data && q.data.length === 0 && <Empty>この月の経費はありません</Empty>}
      {q.data && q.data.length > 0 && (
        <div className="card scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>日付</th>
                <th>勘定科目</th>
                <th>取引先</th>
                <th>内容</th>
                <th className="text-right">金額</th>
                <th>税率</th>
                <th>支払元</th>
                <th>証憑</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((e) => (
                <tr key={e.id} style={{ opacity: e.is_voided ? 0.5 : 1 }}>
                  <td className="num">{fmtDate(e.date)}</td>
                  <td>{accountLabel(e.account_key)}</td>
                  <td>{e.counterparty}</td>
                  <td>
                    {e.description}
                    {e.is_voided && <span className="block text-xs">取消: {e.void_reason}</span>}
                    {e.corrected_from && <span className="block text-xs">（訂正後）</span>}
                  </td>
                  <td className="text-right num">{yen(e.amount)}</td>
                  <td className="num">{toInt(e.tax_rate)}%</td>
                  <td>{e.paid_from_label}</td>
                  <td>{e.evidence ? "あり" : "—"}</td>
                  <td>
                    {can("accounting.bookkeep") && !e.is_voided && (
                      <button className="btn btn-sm" onClick={() => setVoiding(e)}>
                        取消・訂正
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <ExpenseModal onClose={() => setOpen(false)} onDone={async () => { setOpen(false); await q.reload(); }} />}
      {voiding && <VoidExpenseModal row={voiding} onClose={() => setVoiding(null)} onDone={async () => { setVoiding(null); await q.reload(); }} />}
    </div>
  );
}

function ExpenseModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ date: todayJst(), amount: "", tax_rate: "10", account_key: "supplies", counterparty: "", invoice_number: "", description: "", paid_from: "bank" });
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));
  return (
    <Modal
      title="経費を登録"
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
                let evidence: number | null = null;
                if (file) {
                  const ev = await uploadFile<{ id: number }>("/api/accounting/evidences", file, {
                    kind: "receipt",
                    transaction_date: f.date,
                    amount: f.amount,
                    counterparty: f.counterparty,
                    description: f.description,
                  });
                  evidence = ev.id;
                }
                await api("/api/accounting/expenses", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { ...f, evidence } });
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
      <div className="grid grid-cols-2 gap-3">
        <Field label="日付">
          <input className="input" type="date" value={f.date} onChange={(e) => set("date", e.target.value)} />
        </Field>
        <Field label="金額（税込）">
          <input className="input num" value={f.amount} onChange={(e) => set("amount", e.target.value.replace(/[^0-9]/g, ""))} />
        </Field>
        <Field label="勘定科目">
          <select className="input" value={f.account_key} onChange={(e) => set("account_key", e.target.value)}>
            {EXPENSE_ACCOUNTS.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="税率">
          <select className="input" value={f.tax_rate} onChange={(e) => set("tax_rate", e.target.value)}>
            <option value="10">10%</option>
            <option value="8">8%（軽減）</option>
            <option value="0">対象外・非課税</option>
          </select>
        </Field>
        <Field label="取引先">
          <input className="input" value={f.counterparty} onChange={(e) => set("counterparty", e.target.value)} />
        </Field>
        <Field label="登録番号（T＋13桁・任意）">
          <input className="input" value={f.invoice_number} onChange={(e) => set("invoice_number", e.target.value.toUpperCase())} />
        </Field>
        <Field label="支払元">
          <select className="input" value={f.paid_from} onChange={(e) => set("paid_from", e.target.value)}>
            <option value="bank">事業用口座</option>
            <option value="card">事業用カード（後日引落し）</option>
            <option value="office_cash">手許現金（レジ以外）</option>
            <option value="owner_personal">経営者の個人のお金で立替</option>
          </select>
        </Field>
        <Field label="内容">
          <input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="領収書・請求書（PDF・画像）">
          <input className="input" type="file" accept="application/pdf,image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <ErrorBox error={err} />
    </Modal>
  );
}

function VoidExpenseModal({ row, onClose, onDone }: { row: ExpenseRow; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState(String(toInt(row.amount)));
  const [correct, setCorrect] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  return (
    <Modal
      title="経費の取消・訂正"
      onClose={onClose}
      footer={
        <button
          className="btn btn-danger"
          disabled={!reason.trim()}
          onClick={async () => {
            try {
              await api(`/api/accounting/expenses/${row.id}/void`, { method: "POST", body: { reason, correction: correct ? { amount } : undefined } });
              onDone();
            } catch (e) {
              setErr(e);
            }
          }}
        >
          {correct ? "取り消して正しい金額で登録" : "取り消す"}
        </button>
      }
    >
      <div className="space-y-3">
        <p>元の記録は「取消」として残り、仕訳も取消・修正されます（freee に送信済みなら削除・登録し直し）。</p>
        <div className="grid grid-cols-2 gap-2">
          <button className={`btn ${correct ? "btn-primary" : ""}`} onClick={() => setCorrect(true)}>
            金額を訂正
          </button>
          <button className={`btn ${!correct ? "btn-primary" : ""}`} onClick={() => setCorrect(false)}>
            取消のみ
          </button>
        </div>
        {correct && (
          <Field label="正しい金額">
            <input className="input num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ""))} />
          </Field>
        )}
        <Field label="理由（必須）">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

type EvidenceRow = { id: number; kind: string; kind_label: string; original_name: string; size: number; transaction_date: string | null; amount: string | null; counterparty: string; description: string; retention_until: string | null; is_deleted: boolean; deleted_reason: string; uploaded_by_name: string };

function Evidences() {
  const { can } = useSession();
  const [filters, setFilters] = useState({ date_from: "", date_to: "", amount_min: "", amount_max: "", counterparty: "", include_deleted: "" });
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const q = useLoad<EvidenceRow[]>(`/api/accounting/evidences${qs ? `?${qs}` : ""}`, [qs]);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const set = (k: keyof typeof filters, v: string) => setFilters((s) => ({ ...s, [k]: v }));
  return (
    <div className="space-y-3">
      <div className="card-2 grid grid-cols-2 md:grid-cols-6 gap-2 items-end">
        <Field label="取引日（から）">
          <input className="input" type="date" value={filters.date_from} onChange={(e) => set("date_from", e.target.value)} />
        </Field>
        <Field label="取引日（まで）">
          <input className="input" type="date" value={filters.date_to} onChange={(e) => set("date_to", e.target.value)} />
        </Field>
        <Field label="金額（以上）">
          <input className="input num" value={filters.amount_min} onChange={(e) => set("amount_min", e.target.value.replace(/[^0-9]/g, ""))} />
        </Field>
        <Field label="金額（以下）">
          <input className="input num" value={filters.amount_max} onChange={(e) => set("amount_max", e.target.value.replace(/[^0-9]/g, ""))} />
        </Field>
        <Field label="取引先">
          <input className="input" value={filters.counterparty} onChange={(e) => set("counterparty", e.target.value)} />
        </Field>
        {can("accounting.bookkeep") && (
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            ＋ 証憑を保存
          </button>
        )}
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        電子帳簿保存法の検索要件（取引年月日・金額・取引先）で検索できます。削除しても記録とファイルは残り、理由と削除者を記録します。保存期限は取引日から法人10年・個人7年を目安に設定しています（税理士等に確認してください）。
      </p>
      <ErrorBox error={q.error || err} onRetry={() => q.reload(false)} />
      {q.data && q.data.length === 0 && <Empty>証憑はありません</Empty>}
      {q.data && q.data.length > 0 && (
        <div className="card scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>取引日</th>
                <th>種類</th>
                <th>取引先</th>
                <th className="text-right">金額</th>
                <th>内容</th>
                <th>ファイル</th>
                <th>保存期限</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((e) => (
                <tr key={e.id} style={{ opacity: e.is_deleted ? 0.5 : 1 }}>
                  <td className="num">{fmtDate(e.transaction_date)}</td>
                  <td>{e.kind_label}</td>
                  <td>{e.counterparty}</td>
                  <td className="text-right num">{e.amount ? yen(e.amount) : "—"}</td>
                  <td>
                    {e.description}
                    {e.is_deleted && <span className="block text-xs">削除: {e.deleted_reason}</span>}
                  </td>
                  <td>
                    <button className="btn btn-sm btn-ghost" onClick={() => downloadFile(`/api/accounting/evidences/${e.id}/file`, e.original_name).catch(setErr)}>
                      {e.original_name}
                    </button>
                  </td>
                  <td className="num">{fmtDate(e.retention_until)}</td>
                  <td>
                    {can("accounting.bookkeep") && !e.is_deleted && (
                      <button
                        className="btn btn-sm"
                        onClick={async () => {
                          const reason = window.prompt("削除の理由を入力してください（記録は残ります）");
                          if (!reason) return;
                          try {
                            await api(`/api/accounting/evidences/${e.id}/delete`, { method: "POST", body: { reason } });
                            await q.reload();
                          } catch (er) {
                            setErr(er);
                          }
                        }}
                      >
                        削除
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <EvidenceModal onClose={() => setOpen(false)} onDone={async () => { setOpen(false); await q.reload(); }} />}
    </div>
  );
}

function EvidenceModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ kind: "receipt", transaction_date: todayJst(), amount: "", counterparty: "", description: "" });
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));
  return (
    <Modal
      title="証憑を保存"
      onClose={busy ? undefined : onClose}
      footer={
        <button
          className="btn btn-primary"
          disabled={busy || !file}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await uploadFile("/api/accounting/evidences", file!, f);
              onDone();
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          保存する
        </button>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="種類">
          <select className="input" value={f.kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="receipt">領収書</option>
            <option value="invoice">請求書</option>
            <option value="tax_payment">納付証憑</option>
            <option value="contract">契約書</option>
            <option value="statement">明細書・その他</option>
          </select>
        </Field>
        <Field label="取引年月日（必須）">
          <input className="input" type="date" value={f.transaction_date} onChange={(e) => set("transaction_date", e.target.value)} />
        </Field>
        <Field label="金額">
          <input className="input num" value={f.amount} onChange={(e) => set("amount", e.target.value.replace(/[^0-9]/g, ""))} />
        </Field>
        <Field label="取引先">
          <input className="input" value={f.counterparty} onChange={(e) => set("counterparty", e.target.value)} />
        </Field>
        <Field label="内容">
          <input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="ファイル（PDF・画像、10MBまで）">
          <input className="input" type="file" accept="application/pdf,image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <ErrorBox error={err} />
    </Modal>
  );
}

type TransferRow = { id: number; date: string; direction: "in" | "out"; amount: string; method: string; note: string; is_voided: boolean; void_reason: string };

function OwnerTransfers({ from, to }: { from: string; to: string }) {
  const { can } = useSession();
  const q = useLoad<TransferRow[]>(`/api/accounting/owner-transfers?from=${from}&to=${to}`);
  const [f, setF] = useState({ date: todayJst(), direction: "in", amount: "", method: "bank", note: "" });
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        経営者が個人のお金を店に入れた（足りない分を補った）・店のお金を個人に移した記録です。売上・経費にはなりません。個人事業主は事業主借／事業主貸、法人は役員借入金として仕訳します。
      </p>
      {can("accounting.bookkeep") && (
        <div className="card-2 grid grid-cols-2 md:grid-cols-6 gap-2 items-end">
          <Field label="日付">
            <input className="input" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />
          </Field>
          <Field label="向き">
            <select className="input" value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}>
              <option value="in">個人 → 店（投入）</option>
              <option value="out">店 → 個人（引出し）</option>
            </select>
          </Field>
          <Field label="金額">
            <input className="input num" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^0-9]/g, "") })} />
          </Field>
          <Field label="お金の場所">
            <select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
              <option value="bank">事業用口座</option>
              <option value="cash_register">この店舗のレジ現金</option>
            </select>
          </Field>
          <Field label="メモ">
            <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
          </Field>
          <button
            className="btn btn-primary"
            disabled={!f.amount}
            onClick={async () => {
              try {
                await api("/api/accounting/owner-transfers", { method: "POST", idempotencyKey: newIdempotencyKey(), body: f });
                setF({ ...f, amount: "", note: "" });
                await q.reload();
              } catch (e) {
                setErr(e);
              }
            }}
          >
            記録する
          </button>
        </div>
      )}
      <ErrorBox error={q.error || err} />
      {q.data?.map((t) => (
        <div key={t.id} className="card-2 flex flex-wrap gap-3 items-center" style={{ opacity: t.is_voided ? 0.5 : 1 }}>
          <span className="num">{fmtDate(t.date)}</span>
          <span>{t.direction === "in" ? "個人 → 店" : "店 → 個人"}</span>
          <span className="num font-semibold">{yen(t.amount)}</span>
          <span>{t.method === "bank" ? "口座" : "レジ現金"}</span>
          <span className="text-sm">{t.note}</span>
          {t.is_voided ? (
            <span className="text-sm">取消: {t.void_reason}</span>
          ) : (
            can("accounting.bookkeep") && (
              <button
                className="btn btn-sm ml-auto"
                onClick={async () => {
                  const reason = window.prompt("取消の理由");
                  if (!reason) return;
                  try {
                    await api(`/api/accounting/owner-transfers/${t.id}/void`, { method: "POST", body: { reason } });
                    await q.reload();
                  } catch (e) {
                    setErr(e);
                  }
                }}
              >
                取消
              </button>
            )
          )}
        </div>
      ))}
    </div>
  );
}

type CardRow = { id: number; deposit_date: string; sales_amount: string; fee_amount: string; deposit_amount: string; provider_name: string; note: string; is_voided: boolean };

function CardSettlements({ from, to }: { from: string; to: string }) {
  const { can } = useSession();
  const q = useLoad<CardRow[]>(`/api/accounting/card-settlements?from=${from}&to=${to}`);
  const [f, setF] = useState({ deposit_date: todayJst(), sales_amount: "", fee_amount: "", provider_name: "" });
  const [err, setErr] = useState<unknown>(null);
  const deposit = toInt(f.sales_amount) - toInt(f.fee_amount);
  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        カード決済会社から口座に入金された記録です。カード売上（売掛金）と入金・手数料を照合します。
      </p>
      {can("accounting.bookkeep") && (
        <div className="card-2 grid grid-cols-2 md:grid-cols-6 gap-2 items-end">
          <Field label="入金日">
            <input className="input" type="date" value={f.deposit_date} onChange={(e) => setF({ ...f, deposit_date: e.target.value })} />
          </Field>
          <Field label="対象の売上額">
            <input className="input num" value={f.sales_amount} onChange={(e) => setF({ ...f, sales_amount: e.target.value.replace(/[^0-9]/g, "") })} />
          </Field>
          <Field label="決済手数料">
            <input className="input num" value={f.fee_amount} onChange={(e) => setF({ ...f, fee_amount: e.target.value.replace(/[^0-9]/g, "") })} />
          </Field>
          <Field label="入金額">
            <input className="input num" value={String(deposit)} readOnly />
          </Field>
          <Field label="決済会社">
            <input className="input" value={f.provider_name} onChange={(e) => setF({ ...f, provider_name: e.target.value })} />
          </Field>
          <button
            className="btn btn-primary"
            disabled={!f.sales_amount}
            onClick={async () => {
              try {
                await api("/api/accounting/card-settlements", {
                  method: "POST",
                  idempotencyKey: newIdempotencyKey(),
                  body: { ...f, fee_amount: f.fee_amount || "0", deposit_amount: String(deposit) },
                });
                setF({ ...f, sales_amount: "", fee_amount: "" });
                await q.reload();
              } catch (e) {
                setErr(e);
              }
            }}
          >
            記録する
          </button>
        </div>
      )}
      <ErrorBox error={q.error || err} />
      {q.data?.map((c) => (
        <div key={c.id} className="card-2 flex flex-wrap gap-3" style={{ opacity: c.is_voided ? 0.5 : 1 }}>
          <span className="num">{fmtDate(c.deposit_date)}</span>
          <span>{c.provider_name}</span>
          <span className="num">売上 {yen(c.sales_amount)}</span>
          <span className="num">手数料 {yen(c.fee_amount)}</span>
          <span className="num font-semibold">入金 {yen(c.deposit_amount)}</span>
        </div>
      ))}
    </div>
  );
}
