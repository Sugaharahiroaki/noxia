"use client";

/** 年間決算: 決算チェック → 決算整理 → 年間損益（店舗別・合計）→ 年度の締め。 */
import { useState } from "react";
import { Checklist, FreeeHandlesNotice, RequireCaps, Tabs, YearPicker } from "@/components/common";
import { DataTable } from "@/components/DataTable";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { api, downloadFile, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Account, AccountsResponse, FiscalYear } from "@/lib/types_books";
import type { AdjustmentKind, AdjustmentsResponse, ChecklistResponse, YearPl } from "@/lib/types_closing";
import { useLoad } from "@/lib/useLoad";

export default function YearEndPage() {
  const [year, setYear] = useState(new Date().getFullYear());
  const [tab, setTab] = useState("check");
  return (
    <RequireCaps caps={["accounting.view"]}>
      <PageHeader title="年間決算" subtitle="締めていない日・未承認の領収書を片付け、棚卸・減価償却・決算整理を済ませて年間損益を確定し、年度を締めます。" actions={<YearPicker value={year} onChange={setYear} />} />
      <FreeeHandlesNotice what="決算整理と年度の締め" />
      <Tabs tabs={[{ key: "check", label: "決算チェック" }, { key: "adjust", label: "決算整理" }, { key: "pl", label: "年間損益" }, { key: "close", label: "年度を締める" }]} value={tab} onChange={setTab} />
      {tab === "check" && <CheckTab year={year} />}
      {tab === "adjust" && <AdjustTab year={year} />}
      {tab === "pl" && <PlTab year={year} />}
      {tab === "close" && <CloseTab year={year} />}
    </RequireCaps>
  );
}

function CheckTab({ year }: { year: number }) {
  const q = useLoad<ChecklistResponse>(`/api/closing/years/${year}/checklist`, [year]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data!;
  const hints: Record<string, string> = {
    open_days: "「開店・日締め・現金」で締めてください。締めた日だけ仕訳になります。",
    suspense: "レジのその他入出金など内容不明の記録です。「月次損益・経費・証憑」の仕訳で確認し、科目を直してください。",
    pending_receipts: "「経費・領収書管理」で承認・差戻し・却下を済ませてください。",
    unmatched_bank: "「銀行 CSV 取込・入出金照合」で照合するか対象外にしてください（必須ではありません）。",
    inventory_closing: "「固定資産・棚卸」で期末棚卸を確定してください（在庫がなければ 0 円で確定）。",
    depreciation: "「固定資産・棚卸」の減価償却タブで計算してください。",
    inventory_opening: "前年の期末棚卸から今年の期首を作って確定してください。",
    drift: "締めた後に元の記録が変わりました。年度を再開して仕訳を作り直すか、内容を確認してください。",
  };
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="年度" value={`${d.fiscal_year.start_date} 〜 ${d.fiscal_year.end_date}`} />
        <Stat label="申告区分" value={d.fiscal_year.filing_type.startsWith("blue") ? "青色申告" : "白色申告"} />
        <Stat label="状態" value={d.fiscal_year.status_label} accent={d.fiscal_year.is_locked ? "warn" : "ok"} />
        <Stat label="残りの確認" value={`${d.items.filter((i) => i.count > 0).length} 件`} accent={d.items.some((i) => i.blocking && i.count > 0) ? "danger" : "ok"} />
      </div>
      <Checklist items={d.items.map((i) => ({ key: i.code, label: i.label, done: i.count === 0, count: i.count, blocking: i.blocking, hint: i.count ? hints[i.code] : undefined }))} />
    </div>
  );
}

function AdjustTab({ year }: { year: number }) {
  const { can } = useSession();
  const toast = useToast();
  const q = useLoad<AdjustmentsResponse>(`/api/closing/adjustments?year=${year}`, [year]);
  const accounts = useLoad<AccountsResponse>("/api/accounting/accounts?active=1");
  const [open, setOpen] = useState(false);
  if (q.loading || accounts.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data!;
  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        12月分なのに支払いが翌年の費用（未払金）、翌年分を今年払ったもの（前払費用）、自宅兼用の家事分などを登録します。棚卸と減価償却は「固定資産・棚卸」から自動で仕訳になります。
      </p>
      {can("yearend.prepare") && <button className="btn btn-primary" onClick={() => setOpen(true)}>＋ 決算整理を登録</button>}
      <DataTable
        columns={[
          { key: "issue_date", label: "日付", render: (a) => fmtDate(a.issue_date) },
          { key: "kind_label", label: "種類" },
          { key: "description", label: "内容" },
          { key: "debit_account", label: "借方", render: (a) => a.debit_account.name },
          { key: "credit_account", label: "貸方", render: (a) => a.credit_account.name },
          { key: "amount", label: "金額", align: "right", render: (a) => yen(a.amount) },
          {
            key: "ops",
            label: "",
            render: (a) =>
              can("yearend.prepare") && !a.is_voided ? (
                <button
                  className="btn btn-sm"
                  onClick={async () => {
                    const reason = window.prompt("取消の理由");
                    if (!reason) return;
                    try {
                      await api(`/api/closing/adjustments/${a.id}/void`, { method: "POST", body: { reason } });
                      toast.show({ message: "取り消しました" });
                      q.reload();
                    } catch (e) {
                      toast.show({ message: errorMessage(e), tone: "error" });
                    }
                  }}
                >
                  取消
                </button>
              ) : null,
          },
        ]}
        rows={d.adjustments}
        rowKey={(a) => a.id}
        emptyText="決算整理はまだありません。"
      />
      {open && accounts.data && <AdjustmentModal year={year} kinds={d.kinds} accounts={accounts.data.accounts} onClose={() => setOpen(false)} onSaved={() => { setOpen(false); q.reload(); }} />}
    </div>
  );
}

function AdjustmentModal({ year, kinds, accounts, onClose, onSaved }: { year: number; kinds: AdjustmentKind[]; accounts: Account[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const usable = kinds.filter((k) => !["inventory", "depreciation", "opening_cost"].includes(k.key));
  const [kind, setKind] = useState(usable[0]?.key ?? "accrued_expense");
  const k = kinds.find((x) => x.key === kind)!;
  const [debit, setDebit] = useState(k.default_debit);
  const [credit, setCredit] = useState(k.default_credit);
  const [amount, setAmount] = useState("");
  const [desc, setDesc] = useState("");
  const [date, setDate] = useState(`${year}-12-31`);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const opt = (a: Account) => <option key={a.key} value={a.key}>{a.code} {a.name}</option>;
  return (
    <Modal
      title="決算整理を登録"
      onClose={busy ? undefined : onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>やめる</button>
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await api("/api/closing/adjustments", { method: "POST", body: { year, kind, description: desc, amount, debit_key: debit, credit_key: credit, issue_date: date }, idempotencyKey: newIdempotencyKey() });
                toast.show({ message: "決算整理を登録し、仕訳を作りました" });
                onSaved();
              } catch (e) {
                setErr(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "登録中…" : "登録する"}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="種類" hint={k.help}>
          <select
            className="input"
            value={kind}
            onChange={(e) => {
              const nk = kinds.find((x) => x.key === e.target.value)!;
              setKind(nk.key);
              setDebit(nk.default_debit);
              setCredit(nk.default_credit);
            }}
          >
            {usable.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
        </Field>
        <div className="grid md:grid-cols-2 gap-3">
          <Field label="借方（増える科目）"><select className="input" value={debit} onChange={(e) => setDebit(e.target.value)}>{accounts.map(opt)}</select></Field>
          <Field label="貸方（相手科目）"><select className="input" value={credit} onChange={(e) => setCredit(e.target.value)}>{accounts.map(opt)}</select></Field>
          <Field label="金額（円）"><input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} /></Field>
          <Field label="日付（年度内）"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        </div>
        <Field label="内容"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="例: 12月分の電気代（1月支払い）" /></Field>
        {err ? <ErrorBox error={err} /> : null}
      </div>
    </Modal>
  );
}

function PlTab({ year }: { year: number }) {
  const q = useLoad<YearPl>(`/api/closing/years/${year}/pl?by=store`, [year]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data!;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-sm" onClick={() => downloadFile(`/api/closing/years/${year}/pl.xlsx`)}>Excel（年間・月次・店舗別・決算整理・固定資産台帳）</button>
        <button className="btn btn-sm" onClick={() => downloadFile(`/api/closing/years/${year}/pl.pdf`)}>PDF</button>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="収入合計" value={yen(d.total_income)} />
        <Stat label="売上原価" value={yen(d.total_cogs)} />
        <Stat label="経費（売上原価を含む）" value={yen(d.total_expense)} />
        <Stat label="減価償却費" value={yen(d.depreciation_total)} />
        <Stat label="所得金額（控除前）" value={yen(d.profit)} accent={Number(d.profit) < 0 ? "danger" : "ok"} />
      </div>
      {Number(d.suspense_total) !== 0 && <p className="badge badge-danger">仮勘定が残っているため損益は未確定です（決算チェックを確認）</p>}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="card">
          <h2 className="font-bold mb-2">収支内訳書・決算書の行ごと</h2>
          <DataTable columns={[{ key: "label", label: "項目" }, { key: "amount", label: "金額", align: "right", render: (r) => yen(r.amount) }]} rows={d.by_statement_line} rowKey={(r) => r.line} />
        </div>
        <div className="card">
          <h2 className="font-bold mb-2">店舗別（管理用の内訳。申告は事業者単位の合計）</h2>
          <DataTable
            columns={[
              { key: "store", label: "店舗" },
              { key: "income", label: "収入", align: "right", render: (r) => yen(r.income) },
              { key: "expense", label: "経費", align: "right", render: (r) => yen(r.expense) },
              { key: "profit", label: "利益", align: "right", render: (r) => yen(r.profit) },
            ]}
            rows={d.by_store}
            rowKey={(r) => r.store_id}
          />
        </div>
      </div>
      <div className="card">
        <h2 className="font-bold mb-2">月次損益</h2>
        <DataTable
          columns={[
            { key: "month", label: "月" },
            { key: "income", label: "収入", align: "right", render: (r) => yen(r.income) },
            { key: "cogs", label: "売上原価", align: "right", render: (r) => yen(r.cogs) },
            { key: "expense", label: "経費", align: "right", render: (r) => yen(r.expense) },
            { key: "profit", label: "利益", align: "right", render: (r) => yen(r.profit) },
          ]}
          rows={d.month_summary}
          rowKey={(r) => r.month}
        />
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>{d.notice}</p>
    </div>
  );
}

function CloseTab({ year }: { year: number }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const q = useLoad<ChecklistResponse>(`/api/closing/years/${year}/checklist`, [year]);
  const [confirm, setConfirm] = useState<"close" | "reopen" | null>(null);
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const fy: FiscalYear = q.data!.fiscal_year;
  const blocking = q.data!.items.filter((i) => i.blocking && i.count > 0);
  const canClose = can("yearend.confirm");
  async function run(action: "close" | "reopen") {
    setBusy(true);
    setErr(null);
    try {
      await withReauth(() => api(`/api/accounting/fiscal-years/${year}/${action}`, { method: "POST", body: { reason, force }, idempotencyKey: newIdempotencyKey() }));
      toast.show({ message: action === "close" ? `${year}年度を締めました。この年度の仕訳は変更できません。` : `${year}年度を再開しました。` });
      setConfirm(null);
      setReason("");
      q.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <div className="card space-y-2">
        <div className="flex items-center gap-3">
          <span className={`badge ${fy.is_locked ? "badge-warn" : "badge-ok"}`} style={{ fontSize: "1rem" }}>{fy.status_label}</span>
          {fy.closed_at && <span className="text-sm" style={{ color: "var(--color-muted)" }}>締め: {fmtDate(fy.closed_at)}　再開 {fy.reopen_count} 回</span>}
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          年度を締めると、その年の仕訳は作り直し・追加・取消ができなくなり、確定申告の数字が固定されます。締めた後に元の記録が変わると「締め後の変更」として記録されます。締め・再開はオーナー本人がパスワードを再確認して行います。
        </p>
        {!canClose && <p className="badge badge-warn">年度の締め・再開はオーナーだけが行えます。</p>}
        {!fy.is_locked && blocking.length > 0 && (
          <div className="card-2" role="alert">
            <p className="font-semibold">締める前に確認が必要な項目があります</p>
            <ul className="text-sm mt-1">{blocking.map((b) => <li key={b.code}>・{b.label}（{b.count} 件）</li>)}</ul>
            {canClose && (
              <label className="flex items-center gap-2 mt-2 text-sm"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} style={{ width: 20, height: 20 }} /> 確認したうえで、このまま締める</label>
            )}
          </div>
        )}
        {canClose && (
          <div className="flex gap-2">
            {!fy.is_locked && <button className="btn btn-primary btn-lg" disabled={blocking.length > 0 && !force} onClick={() => setConfirm("close")}>{year}年度を締める</button>}
            {fy.is_locked && <button className="btn btn-lg" onClick={() => setConfirm("reopen")}>{year}年度を再開する（理由を記録）</button>}
          </div>
        )}
        {err ? <ErrorBox error={err} /> : null}
      </div>
      {fy.events?.length > 0 && (
        <div className="card">
          <h2 className="font-bold mb-2">履歴</h2>
          <ul className="text-sm space-y-1">{fy.events.map((e, i) => <li key={i}>{fmtDate(e.at)} {e.action === "close" ? "締め" : e.action === "reopen" ? "再開" : e.action} {e.by} {e.reason && `「${e.reason}」`}</li>)}</ul>
        </div>
      )}
      {fy.drift_warnings?.length > 0 && (
        <div className="card">
          <h2 className="font-bold mb-2">締め後に元の記録が変わったもの</h2>
          <ul className="text-sm space-y-1">{fy.drift_warnings.map((w) => <li key={w.sync_key}>{fmtDate(w.detected_at)} {w.message}</li>)}</ul>
        </div>
      )}
      {confirm && (
        <ConfirmDialog
          title={confirm === "close" ? `${year}年度を締めます` : `${year}年度を再開します`}
          confirmLabel={confirm === "close" ? "締める" : "再開する"}
          danger={confirm === "reopen"}
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => run(confirm)}
        >
          <Field label={confirm === "close" ? "メモ（任意）" : "再開の理由（必須）"}>
            <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={confirm === "reopen" ? "例: 経費の登録漏れ" : ""} />
          </Field>
        </ConfirmDialog>
      )}
    </div>
  );
}
