"use client";

/**
 * 銀行: 口座 ／ CSV 取込（プレビュー→列の対応→取込→確定）／ 入出金照合（候補の確定・対象外・振替・解除）／ 取込履歴。
 * 銀行明細は「照合するだけ」で売上・経費にはしない。カード入金の確定だけが入金記録（手数料つき）を作る。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { FreeeHandlesNotice, MonthPicker, ReasonPicker, RequireCaps, Tabs } from "@/components/common";
import { DataTable } from "@/components/DataTable";
import { Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { monthRange, thisMonth } from "@/lib/accounting";
import { api, errorMessage, newIdempotencyKey, uploadFile } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { BankAccount, BankImport, BankSummary, BankTransaction, Candidate, ImportPreview } from "@/lib/types_banking";
import { useLoad } from "@/lib/useLoad";

const TABS = [
  { key: "reconcile", label: "入出金照合" },
  { key: "import", label: "CSV 取込" },
  { key: "accounts", label: "口座" },
  { key: "history", label: "取込履歴" },
];

const STATUS_BADGE: Record<string, string> = {
  unmatched: "badge badge-warn",
  suggested: "badge badge-accent",
  matched: "badge badge-ok",
  partially_matched: "badge badge-orange",
  ignored: "badge",
  transfer: "badge badge-ok",
};

export default function BankPage() {
  const [tab, setTab] = useState("reconcile");
  const accounts = useLoad<BankAccount[]>("/api/banking/accounts");
  return (
    <RequireCaps caps={["banking.view"]}>
      <PageHeader title="銀行 CSV 取込・入出金照合" subtitle="ネットバンキングの CSV を取り込み、カード入金・経費・スタッフへの振込などと突き合わせます。明細はそのまま売上や経費にはなりません。" />
      <FreeeHandlesNotice what="銀行明細の取込と照合" />
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {accounts.loading && <Spinner />}
      {accounts.error ? <ErrorBox error={accounts.error} onRetry={() => accounts.reload(false)} /> : null}
      {accounts.data && (
        <>
          {tab === "accounts" && <AccountsTab accounts={accounts.data} onChanged={() => accounts.reload()} />}
          {tab === "import" && <ImportTab accounts={accounts.data} />}
          {tab === "reconcile" && <ReconcileTab accounts={accounts.data} />}
          {tab === "history" && <HistoryTab accounts={accounts.data} />}
        </>
      )}
    </RequireCaps>
  );
}

// --- 口座 -----------------------------------------------------------------------------------
function AccountsTab({ accounts, onChanged }: { accounts: BankAccount[]; onChanged: () => void }) {
  const { can } = useSession();
  const [edit, setEdit] = useState<Partial<BankAccount> | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function save() {
    if (!edit) return;
    setBusy(true);
    setErr(null);
    try {
      if (edit.id) await api(`/api/banking/accounts/${edit.id}`, { method: "PATCH", body: edit });
      else await api("/api/banking/accounts", { method: "POST", body: edit, idempotencyKey: newIdempotencyKey() });
      toast.show({ message: "口座を保存しました" });
      setEdit(null);
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {can("banking.import") && (
        <button className="btn btn-primary" onClick={() => setEdit({ name: "", bank_name: "", bank_code: "", branch_name: "", branch_code: "", account_number_last4: "", holder_name: "", is_business: true })}>
          ＋ 口座を登録
        </button>
      )}
      <DataTable
        columns={[
          { key: "name", label: "表示名" },
          { key: "bank_name", label: "銀行" },
          { key: "branch_name", label: "支店" },
          { key: "account_type_label", label: "種類" },
          { key: "account_number_last4", label: "口座番号（下4桁）", render: (a) => (a.account_number_last4 ? `****${a.account_number_last4}` : "") },
          { key: "holder_name", label: "名義" },
          { key: "is_business", label: "用途", render: (a) => (a.is_business ? "事業専用" : <span className="badge badge-warn">家事兼用</span>) },
          { key: "is_active", label: "", render: (a) => (a.is_active ? "" : <span className="badge">停止</span>) },
        ]}
        rows={accounts}
        rowKey={(a) => a.id}
        onRowClick={can("banking.import") ? (a) => setEdit(a) : undefined}
        emptyText="口座がまだありません。「口座を登録」から追加してください。"
      />
      {edit && (
        <Modal
          title={edit.id ? "口座を編集" : "口座を登録"}
          onClose={busy ? undefined : () => setEdit(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEdit(null)} disabled={busy}>やめる</button>
              <button className="btn btn-primary" onClick={save} disabled={busy || !edit.name}>{busy ? "保存中…" : "保存"}</button>
            </>
          }
        >
          <ErrorBox error={err} />
          <div className="grid md:grid-cols-2 gap-3">
            <Field label="表示名（例: 京信 事業用）"><input className="input" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="銀行名"><input className="input" value={edit.bank_name ?? ""} onChange={(e) => setEdit({ ...edit, bank_name: e.target.value })} /></Field>
            <Field label="銀行コード（4桁）"><input className="input" value={edit.bank_code ?? ""} onChange={(e) => setEdit({ ...edit, bank_code: e.target.value })} /></Field>
            <Field label="支店名"><input className="input" value={edit.branch_name ?? ""} onChange={(e) => setEdit({ ...edit, branch_name: e.target.value })} /></Field>
            <Field label="支店コード（3桁）"><input className="input" value={edit.branch_code ?? ""} onChange={(e) => setEdit({ ...edit, branch_code: e.target.value })} /></Field>
            <Field label="種類">
              <select className="input" value={edit.account_type ?? "ordinary"} onChange={(e) => setEdit({ ...edit, account_type: e.target.value as BankAccount["account_type"] })}>
                <option value="ordinary">普通</option>
                <option value="checking">当座</option>
                <option value="savings">貯蓄</option>
              </select>
            </Field>
            <Field label="口座番号の下4桁" hint="全桁は保存しません"><input className="input" maxLength={4} value={edit.account_number_last4 ?? ""} onChange={(e) => setEdit({ ...edit, account_number_last4: e.target.value })} /></Field>
            <Field label="名義"><input className="input" value={edit.holder_name ?? ""} onChange={(e) => setEdit({ ...edit, holder_name: e.target.value })} /></Field>
            <Field label="取込開始時の残高" hint="最初の CSV の前の残高（残高のつながりの確認に使います）"><input className="input" inputMode="numeric" value={edit.opening_balance ?? ""} onChange={(e) => setEdit({ ...edit, opening_balance: e.target.value })} /></Field>
            <Field label="残高の基準日"><input className="input" type="date" value={edit.opening_balance_date ?? ""} onChange={(e) => setEdit({ ...edit, opening_balance_date: e.target.value || null })} /></Field>
          </div>
          <label className="flex items-center gap-2 mt-3">
            <input type="checkbox" checked={edit.is_business ?? true} onChange={(e) => setEdit({ ...edit, is_business: e.target.checked })} style={{ width: 22, height: 22 }} />
            事業専用の口座（家事と兼用なら外す。個人の支出は照合で「対象外」にします）
          </label>
          {edit.id && (
            <label className="flex items-center gap-2 mt-2">
              <input type="checkbox" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} style={{ width: 22, height: 22 }} />
              使用中
            </label>
          )}
        </Modal>
      )}
    </div>
  );
}

// --- CSV 取込 --------------------------------------------------------------------------------
function ImportTab({ accounts }: { accounts: BankAccount[] }) {
  const { can } = useSession();
  const toast = useToast();
  const [accountId, setAccountId] = useState<number | "">(accounts[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [cmap, setCmap] = useState<Record<string, string>>({});
  const [headerRows, setHeaderRows] = useState("");
  const [imported, setImported] = useState<BankImport | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [idemKey, setIdemKey] = useState(newIdempotencyKey());

  const fields = useMemo(() => {
    const f: Record<string, string> = {};
    const anyMap = Object.values(cmap).some((v) => v !== "");
    if (anyMap) f.column_map = JSON.stringify(Object.fromEntries(Object.entries(cmap).map(([k, v]) => [k, v === "" ? null : Number(v)])));
    if (headerRows) f.header_rows = headerRows;
    return f;
  }, [cmap, headerRows]);

  async function doPreview(withMap = false) {
    if (!file || !accountId) return;
    setBusy(true);
    setErr(null);
    setImported(null);
    try {
      const p = await uploadFile<ImportPreview>("/api/banking/imports/preview", file, { bank_account: String(accountId), ...(withMap ? fields : {}) });
      setPreview(p);
      if (!withMap) setCmap(Object.fromEntries(Object.entries(p.column_map).map(([k, v]) => [k, v === null || v === undefined ? "" : String(v)])));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    if (!file || !accountId) return;
    setBusy(true);
    setErr(null);
    try {
      const imp = await uploadFile<BankImport>("/api/banking/imports", file, { bank_account: String(accountId), ...fields }, { idempotencyKey: idemKey });
      setImported(imp);
      toast.show({ message: `${imp.rows_imported} 行を読み込みました。内容を確認して「確定」を押してください。`, tone: "info" });
    } catch (e) {
      setErr(e);
      setIdemKey(newIdempotencyKey());
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!imported) return;
    setBusy(true);
    setErr(null);
    try {
      const imp = await api<BankImport>(`/api/banking/imports/${imported.id}/commit`, { method: "POST", body: {}, idempotencyKey: newIdempotencyKey() });
      setImported(imp);
      toast.show({ message: "取り込みを確定しました。照合の候補を計算しました。" });
      setFile(null);
      setPreview(null);
      setIdemKey(newIdempotencyKey());
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function rollback() {
    if (!imported) return;
    setBusy(true);
    try {
      await api(`/api/banking/imports/${imported.id}/rollback`, { method: "POST", body: { reason: "取込をやり直す" }, idempotencyKey: newIdempotencyKey() });
      setImported(null);
      setIdemKey(newIdempotencyKey());
      toast.show({ message: "取り込みを取り消しました", tone: "info" });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  if (!can("banking.import")) return <Empty>CSV の取込は記帳の権限が必要です。</Empty>;
  if (!accounts.length) return <Empty>先に「口座」タブで銀行口座を登録してください。</Empty>;

  const balanceBadge = (check: string) =>
    check === "ok" ? <span className="badge badge-ok">残高がつながっています</span> : check === "mismatch" ? <span className="badge badge-danger">残高がつながりません</span> : <span className="badge">残高は確認できません</span>;

  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <div className="grid md:grid-cols-3 gap-3">
          <Field label="口座">
            <select className="input" value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </Field>
          <Field label="CSV ファイル（ネットバンキングからダウンロードしたもの）">
            <input className="input" type="file" accept=".csv,text/csv,text/plain" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setImported(null); }} />
          </Field>
          <div className="flex items-end">
            <button className="btn btn-primary w-full" disabled={!file || busy} onClick={() => doPreview(false)}>{busy ? "読み込み中…" : "内容を確認する"}</button>
          </div>
        </div>
        <ErrorBox error={err} />
      </div>

      {preview && (
        <div className="card space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="badge">文字コード: {preview.encoding}</span>
            <span className="badge">読み取れた行: {preview.rows_parsed} / {preview.rows_total}</span>
            {preview.rows_duplicate > 0 && <span className="badge badge-warn">すでに取込済みの行: {preview.rows_duplicate}</span>}
            {preview.guessed ? <span className="badge badge-warn">列の対応は推測です。確認してください</span> : <span className="badge badge-ok">保存済みの列の対応を使いました</span>}
            {balanceBadge(preview.balance_check)}
            {preview.period_start && <span className="badge">{fmtDate(preview.period_start)} 〜 {preview.period_end ? fmtDate(preview.period_end) : ""}</span>}
          </div>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>{preview.balance_note}</p>
          {preview.errors.length > 0 && (
            <div className="card-2" role="alert">
              <div className="font-semibold">読めなかった行（取り込まれません）</div>
              <ul className="text-sm">
                {preview.errors.slice(0, 10).map((e, i) => (
                  <li key={i}>{e.row !== null ? `${e.row} 行目: ` : ""}{e.message}</li>
                ))}
              </ul>
            </div>
          )}
          <h3 className="font-bold">列の対応</h3>
          <div className="grid md:grid-cols-3 lg:grid-cols-6 gap-2">
            {preview.columns.map((c) => (
              <Field key={c.key} label={c.label}>
                <select className="input" style={{ minHeight: 44 }} value={cmap[c.key] ?? ""} onChange={(e) => setCmap({ ...cmap, [c.key]: e.target.value })}>
                  <option value="">（なし）</option>
                  {(preview.header.length ? preview.header : preview.preview[0] ?? []).map((h, i) => (
                    <option key={i} value={i}>{i + 1}: {h || "（空）"}</option>
                  ))}
                </select>
              </Field>
            ))}
            <Field label="見出しまでの行数（空＝自動）"><input className="input" style={{ minHeight: 44 }} inputMode="numeric" value={headerRows} onChange={(e) => setHeaderRows(e.target.value)} /></Field>
          </div>
          <div className="flex gap-2">
            <button className="btn" disabled={busy} onClick={() => doPreview(true)}>この対応で読み直す</button>
          </div>
          <h3 className="font-bold">先頭の明細（確認用）</h3>
          <div className="scroll-x" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>日付</th><th>摘要</th><th>相手</th><th className="text-right">入金</th><th className="text-right">出金</th><th className="text-right">残高</th><th></th></tr></thead>
              <tbody>
                {preview.sample.map((r) => (
                  <tr key={r.row_no} style={{ opacity: r.duplicate ? 0.5 : 1 }}>
                    <td>{fmtDate(r.date)}</td><td>{r.description}</td><td>{r.counterparty}</td>
                    <td className="text-right num">{Number(r.amount_in) ? yen(r.amount_in) : ""}</td>
                    <td className="text-right num">{Number(r.amount_out) ? yen(r.amount_out) : ""}</td>
                    <td className="text-right num">{r.balance !== null ? yen(r.balance) : ""}</td>
                    <td>{r.duplicate && <span className="badge">取込済み</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary className="cursor-pointer">元のファイルの先頭 20 行</summary>
            <div className="scroll-x" style={{ overflowX: "auto" }}>
              <table className="table text-sm">
                <tbody>
                  {preview.preview.map((r, i) => (
                    <tr key={i} style={{ background: i === preview.header_row ? "var(--color-panel3)" : undefined }}>
                      <td style={{ color: "var(--color-muted)" }}>{i + 1}</td>
                      {r.map((c, j) => <td key={j}>{c}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          {!imported && (
            <button className="btn btn-lg btn-primary" disabled={busy || preview.rows_parsed - preview.rows_duplicate === 0} onClick={doImport}>
              {busy ? "取り込み中…" : `${preview.rows_parsed - preview.rows_duplicate} 行を取り込む`}
            </button>
          )}
        </div>
      )}

      {imported && (
        <div className="card space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`badge ${imported.status === "committed" ? "badge-ok" : imported.status === "parsed" ? "badge-accent" : ""}`}>{imported.status_label}</span>
            <span className="badge">取込 {imported.rows_imported} 行</span>
            {imported.rows_duplicate > 0 && <span className="badge badge-warn">重複で除外 {imported.rows_duplicate} 行</span>}
            {imported.rows_error > 0 && <span className="badge badge-danger">読めない行 {imported.rows_error}</span>}
            {balanceBadge(imported.balance_check)}
          </div>
          {imported.status === "parsed" && (
            <div className="flex gap-3">
              <button className="btn btn-lg btn-ok" disabled={busy} onClick={commit}>確定する（照合に進む）</button>
              <button className="btn btn-lg" disabled={busy} onClick={rollback}>取り消してやり直す</button>
            </div>
          )}
          {imported.status === "committed" && <p>取り込みが終わりました。「入出金照合」タブで候補を確認してください。</p>}
        </div>
      )}
    </div>
  );
}

// --- 照合 -----------------------------------------------------------------------------------
const IGNORE_REASONS = ["家事費（個人の支出）", "個人の入金", "口座の利息・手数料（別に記録済み）", "二重に取り込んだ行"];

function ReconcileTab({ accounts }: { accounts: BankAccount[] }) {
  const { can } = useSession();
  const toast = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [status, setStatus] = useState("open");
  const [accountId, setAccountId] = useState("");
  const [page, setPage] = useState(1);
  const [active, setActive] = useState<BankTransaction | null>(null);
  const range = monthRange(month);
  const q = `from=${range.from}&to=${range.to}&page=${page}&page_size=50` + (status ? `&status=${status}` : "") + (accountId ? `&bank_account=${accountId}` : "");
  const list = useLoad<{ items: BankTransaction[]; total: number; page: number; page_size: number }>(`/api/banking/transactions?${q}`, [q]);
  const summary = useLoad<BankSummary>(`/api/banking/summary?from=${range.from}&to=${range.to}`, [range.from, range.to]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const refresh = useCallback(() => {
    list.reload();
    summary.reload();
  }, [list, summary]);

  async function scan() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ scanned: number; with_candidates: number }>(`/api/banking/scan?from=${range.from}&to=${range.to}`, { method: "POST", body: {} });
      toast.show({ message: `${r.scanned} 件を確認し、${r.with_candidates} 件に候補が付きました`, tone: "info" });
      refresh();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <MonthPicker value={month} onChange={(m) => { setMonth(m); setPage(1); }} />
        <select className="input" style={{ minHeight: 44, width: 200 }} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="open">未照合・候補あり・一部</option>
          <option value="">すべて</option>
          <option value="unmatched">未照合</option>
          <option value="suggested">候補あり</option>
          <option value="matched">照合済み</option>
          <option value="ignored">対象外</option>
          <option value="transfer">振替</option>
        </select>
        <select className="input" style={{ minHeight: 44, width: 220 }} value={accountId} onChange={(e) => { setAccountId(e.target.value); setPage(1); }}>
          <option value="">すべての口座</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {can("banking.reconcile") && <button className="btn" disabled={busy} onClick={scan}>候補を再計算</button>}
      </div>
      {summary.data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat label="未照合の件数" value={summary.data.unmatched_count} accent={summary.data.unmatched_count ? "warn" : "ok"} />
          <Stat label="未照合の入金" value={yen(summary.data.unmatched_in)} />
          <Stat label="未照合の出金" value={yen(summary.data.unmatched_out)} />
          <div className="card-2 text-sm">
            <div style={{ color: "var(--color-muted)" }}>口座の残高（明細 / 帳簿）</div>
            {summary.data.accounts.map((a) => (
              <div key={a.id} className="flex justify-between gap-2">
                <span>{a.name}</span>
                <span className="num">{a.statement_balance !== null ? yen(a.statement_balance) : "—"} / {yen(a.book_balance)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <ErrorBox error={err ?? list.error} />
      {list.loading && !list.data ? (
        <Spinner />
      ) : (
        <DataTable
          columns={[
            { key: "date", label: "日付", render: (t) => fmtDate(t.date), width: 110 },
            { key: "bank_account_name", label: "口座" },
            { key: "description", label: "摘要", render: (t) => <span>{t.description}{t.counterparty && t.counterparty !== t.description ? <span style={{ color: "var(--color-muted)" }}> {t.counterparty}</span> : null}</span> },
            { key: "amount_in", label: "入金", align: "right", render: (t) => (Number(t.amount_in) ? yen(t.amount_in) : "") },
            { key: "amount_out", label: "出金", align: "right", render: (t) => (Number(t.amount_out) ? yen(t.amount_out) : "") },
            { key: "balance", label: "残高", align: "right", render: (t) => (t.balance !== null ? yen(t.balance) : "") },
            { key: "status", label: "状態", render: (t) => <span className={STATUS_BADGE[t.status] ?? "badge"}>{t.status_label}</span> },
            { key: "recs", label: "照合先", render: (t) => t.reconciliations.filter((r) => r.status === "confirmed").map((r) => r.label || r.match_type_label).join(" / ") || (t.ignore_reason ? `対象外: ${t.ignore_reason}` : "") },
          ]}
          rows={list.data?.items ?? []}
          rowKey={(t) => t.id}
          onRowClick={(t) => setActive(t)}
          activeKey={active?.id ?? null}
          page={page}
          pageSize={50}
          total={list.data?.total}
          onPageChange={setPage}
          keyboard
          emptyText="この条件の明細はありません。「CSV 取込」から明細を取り込んでください。"
        />
      )}
      {active && <TransactionDialog tx={active} onClose={() => setActive(null)} onChanged={() => { setActive(null); refresh(); }} />}
    </div>
  );
}

function TransactionDialog({ tx, onClose, onChanged }: { tx: BankTransaction; onClose: () => void; onChanged: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const detail = useLoad<{ transaction: BankTransaction; candidates: Candidate[] }>(`/api/banking/transactions/${tx.id}/candidates`, [tx.id]);
  const [mode, setMode] = useState<"candidates" | "ignore" | "void" | "transfer">("candidates");
  const [reason, setReason] = useState("");
  const [voidId, setVoidId] = useState<number | null>(null);
  const [counterId, setCounterId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const canRec = can("banking.reconcile");
  const t = detail.data?.transaction ?? tx;
  const confirmed = t.reconciliations.filter((r) => r.status === "confirmed");

  useEffect(() => setErr(null), [mode]);

  async function act(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      toast.show({ message });
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const confirmCandidate = (c: Candidate) =>
    act(() => api(`/api/banking/transactions/${tx.id}/reconcile`, { method: "POST", body: { allocations: [c] }, idempotencyKey: newIdempotencyKey() }), "照合を確定しました");

  return (
    <Modal
      title={`${fmtDate(t.date)} ${Number(t.amount_in) ? `入金 ${yen(t.amount_in)}` : `出金 ${yen(t.amount_out)}`}`}
      onClose={busy ? undefined : onClose}
      wide
      footer={<button className="btn" onClick={onClose} disabled={busy}>閉じる</button>}
    >
      <div className="space-y-3">
        <div className="card-2">
          <div className="font-semibold">{t.description}</div>
          <div className="text-sm" style={{ color: "var(--color-muted)" }}>
            {t.bank_account_name}{t.counterparty ? ` ・ 相手: ${t.counterparty}` : ""}{t.balance !== null ? ` ・ 残高 ${yen(t.balance)}` : ""} ・ <span className={STATUS_BADGE[t.status]}>{t.status_label}</span>
          </div>
          {t.ignore_reason && <div className="text-sm">対象外の理由: {t.ignore_reason}</div>}
        </div>
        <ErrorBox error={err} />
        {confirmed.length > 0 && (
          <div className="card-2">
            <div className="font-semibold mb-1">確定した照合</div>
            {confirmed.map((r) => (
              <div key={r.id} className="flex items-center justify-between gap-2 py-1" style={{ borderBottom: "1px solid var(--color-border)" }}>
                <span>
                  <span className="badge badge-ok mr-2">{r.match_type_label}</span>
                  {r.label} {yen(r.amount)}{Number(r.fee_amount) ? `（手数料 ${yen(r.fee_amount)}）` : ""}
                </span>
                {canRec && (
                  <button className="btn btn-sm" onClick={() => { setVoidId(r.id); setMode("void"); }}>消込を解除</button>
                )}
              </div>
            ))}
          </div>
        )}
        {mode === "void" && voidId !== null && (
          <div className="card-2 space-y-2">
            <div className="font-semibold">消込を解除する理由</div>
            <ReasonPicker options={["別の取引だった", "金額が違う", "重複して照合した"]} value={reason} onChange={setReason} />
            <div className="flex gap-2">
              <button className="btn btn-danger" disabled={busy || !reason} onClick={() => act(() => api(`/api/banking/reconciliations/${voidId}/void`, { method: "POST", body: { reason }, idempotencyKey: newIdempotencyKey() }), "消込を解除しました")}>解除する</button>
              <button className="btn" onClick={() => setMode("candidates")}>やめる</button>
            </div>
          </div>
        )}
        {mode === "candidates" && t.status !== "ignored" && t.status !== "matched" && t.status !== "transfer" && (
          <div className="space-y-2">
            <div className="font-semibold">照合の候補</div>
            {detail.loading && <Spinner />}
            {detail.data && detail.data.candidates.length === 0 && (
              <Empty>候補が見つかりません。経費・振込・カード入金の記録がまだ無いか、金額が合わない可能性があります。該当の記録を登録してから「候補を再計算」してください。</Empty>
            )}
            {detail.data?.candidates.map((c, i) => (
              <div key={i} className="card-2 flex items-start justify-between gap-3">
                <div>
                  <div><span className="badge badge-accent mr-2">{labelOf(c.match_type)}</span>{c.label}</div>
                  <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                    金額 {yen(c.amount)}{Number(c.fee_amount) ? ` ・ 手数料差 ${yen(c.fee_amount)}` : ""} ・ 一致度 {Math.round(c.score * 100)}% ・ ルール {c.rule}
                  </div>
                </div>
                {canRec && <button className="btn btn-ok" disabled={busy} onClick={() => confirmCandidate(c)}>確定</button>}
              </div>
            ))}
            {canRec && (
              <div className="flex flex-wrap gap-2 pt-2">
                <button className="btn" onClick={() => setMode("ignore")}>対象外にする（家事費など）</button>
                <button className="btn" onClick={() => setMode("transfer")}>自社口座間の振替として結ぶ</button>
              </div>
            )}
          </div>
        )}
        {mode === "ignore" && (
          <div className="card-2 space-y-2">
            <div className="font-semibold">対象外にする理由</div>
            <ReasonPicker options={IGNORE_REASONS} value={reason} onChange={setReason} />
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={busy || !reason} onClick={() => act(() => api(`/api/banking/transactions/${tx.id}/ignore`, { method: "POST", body: { reason }, idempotencyKey: newIdempotencyKey() }), "対象外にしました")}>対象外にする</button>
              <button className="btn" onClick={() => setMode("candidates")}>やめる</button>
            </div>
          </div>
        )}
        {t.status === "ignored" && canRec && (
          <button className="btn" disabled={busy} onClick={() => act(() => api(`/api/banking/transactions/${tx.id}/ignore`, { method: "POST", body: { undo: true }, idempotencyKey: newIdempotencyKey() }), "対象外を取り消しました")}>対象外を取り消す</button>
        )}
        {mode === "transfer" && (
          <div className="card-2 space-y-2">
            <div className="font-semibold">相手の明細の ID（別口座の、同じ金額で反対向きの明細）</div>
            <input className="input" inputMode="numeric" value={counterId} onChange={(e) => setCounterId(e.target.value)} placeholder="候補の一覧に出た振替の明細を選ぶか、ID を入力" />
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={busy || !counterId} onClick={() => act(() => api(`/api/banking/transactions/${tx.id}/transfer`, { method: "POST", body: { counter_transaction_id: Number(counterId) }, idempotencyKey: newIdempotencyKey() }), "振替として結びました")}>振替にする</button>
              <button className="btn" onClick={() => setMode("candidates")}>やめる</button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function labelOf(matchType: string): string {
  return (
    {
      card_settlement: "カード売上の入金",
      bank_deposit: "売上金の預入",
      employee_payment: "スタッフへの振込",
      expense: "経費の支払い",
      payable_payment: "買掛金の支払い",
      owner_transfer: "経営者との資金移動",
      tax_remittance: "源泉所得税の納付",
      transfer: "口座間の振替",
      manual_journal: "手入力の仕訳",
    } as Record<string, string>
  )[matchType] ?? matchType;
}

// --- 履歴 -----------------------------------------------------------------------------------
function HistoryTab({ accounts }: { accounts: BankAccount[] }) {
  const { can } = useSession();
  const toast = useToast();
  const list = useLoad<BankImport[]>("/api/banking/imports");
  const [err, setErr] = useState<unknown>(null);
  const names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  async function rollback(imp: BankImport) {
    if (!window.confirm(`${imp.original_name} の取込を取り消しますか？（照合済みの明細があると取り消せません）`)) return;
    try {
      await api(`/api/banking/imports/${imp.id}/rollback`, { method: "POST", body: { reason: "履歴から取消" }, idempotencyKey: newIdempotencyKey() });
      toast.show({ message: "取込を取り消しました", tone: "info" });
      list.reload();
    } catch (e) {
      setErr(e);
    }
  }
  if (list.loading) return <Spinner />;
  return (
    <div className="space-y-3">
      <ErrorBox error={err ?? list.error} />
      <DataTable
        columns={[
          { key: "created_at", label: "取込日時", render: (i) => new Date(i.created_at).toLocaleString("ja-JP") },
          { key: "bank_account", label: "口座", render: (i) => names[i.bank_account] ?? i.bank_account_name },
          { key: "original_name", label: "ファイル" },
          { key: "period", label: "期間", render: (i) => (i.period_start ? `${fmtDate(i.period_start)} 〜 ${i.period_end ? fmtDate(i.period_end) : ""}` : "") },
          { key: "rows_imported", label: "取込行", align: "right" },
          { key: "rows_duplicate", label: "重複", align: "right" },
          { key: "balance_check", label: "残高", render: (i) => (i.balance_check === "ok" ? <span className="badge badge-ok">OK</span> : i.balance_check === "mismatch" ? <span className="badge badge-danger">不一致</span> : <span className="badge">—</span>) },
          { key: "status", label: "状態", render: (i) => <span className={`badge ${i.status === "committed" ? "badge-ok" : i.status === "parsed" ? "badge-accent" : ""}`}>{i.status_label}</span> },
          { key: "imported_by_name", label: "担当" },
          { key: "actions", label: "", render: (i) => (can("banking.import") && i.status !== "rolled_back" ? <button className="btn btn-sm" onClick={() => rollback(i)}>取消</button> : null) },
        ]}
        rows={list.data ?? []}
        rowKey={(i) => i.id}
        emptyText="取込履歴はありません。"
      />
    </div>
  );
}
