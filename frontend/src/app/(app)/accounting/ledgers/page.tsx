"use client";

/** 売上・現金・預金・売掛・買掛・仕入・経費・スタッフ支払・試算表・総勘定元帳（すべて仕訳行から作る帳簿） */
import { useMemo, useState } from "react";
import { MonthPicker, RequireCaps, Tabs } from "@/components/common";
import { DataTable, type Column } from "@/components/DataTable";
import { Empty, ErrorBox, PageHeader, Spinner, Stat } from "@/components/ui";
import { monthRange, thisMonth } from "@/lib/accounting";
import { downloadFile } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import type { Account, AccountsResponse, LedgerResponse, LedgerRow, PayablesBook, ReceivablesBook, SalesBook, StaffPaymentsBook, TrialBalance } from "@/lib/types_books";
import { useLoad } from "@/lib/useLoad";

const TABS = [
  { key: "sales", label: "売上帳" },
  { key: "cash", label: "現金出納帳" },
  { key: "bank", label: "預金出納帳" },
  { key: "receivables", label: "売掛帳" },
  { key: "payables", label: "買掛帳" },
  { key: "purchases", label: "仕入帳" },
  { key: "expenses", label: "経費帳" },
  { key: "staff_payments", label: "スタッフ支払台帳" },
  { key: "trial_balance", label: "試算表" },
  { key: "general", label: "総勘定元帳" },
];

export default function LedgersPage() {
  const [tab, setTab] = useState("sales");
  const [month, setMonth] = useState(thisMonth());
  const [account, setAccount] = useState("cash");
  const range = monthRange(month);
  const accounts = useLoad<AccountsResponse>("/api/accounting/accounts?active=1");
  const q = `from=${range.from}&to=${range.to}` + (tab === "general" ? `&account=${account}` : "");
  const path = `/api/accounting/ledgers/${tab}?${q}`;

  return (
    <RequireCaps caps={["accounting.view"]}>
      <PageHeader
        title="帳簿"
        subtitle="締め済みの営業日・承認済みの経費・銀行の照合・決算整理から作った仕訳の集計です。別のテーブルに転記していないので、仕訳と必ず一致します。"
        actions={
          <>
            <MonthPicker value={month} onChange={setMonth} />
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/ledgers/${tab}.csv?${q}`)}>
              CSV
            </button>
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/ledgers/${tab}.xlsx?${q}`)}>
              Excel
            </button>
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/accounting/ledgers.xlsx?from=${range.from}&to=${range.to}`)}>
              全帳簿を Excel で
            </button>
          </>
        }
      />
      <Tabs tabs={TABS} value={tab} onChange={setTab} size="sm" />
      {tab === "general" && accounts.data && (
        <div className="mb-3 flex items-center gap-2">
          <span className="label mb-0">科目</span>
          <select className="input" style={{ minHeight: 44, width: 280 }} value={account} onChange={(e) => setAccount(e.target.value)}>
            {accounts.data.accounts.map((a: Account) => (
              <option key={a.key} value={a.key}>
                {a.code} {a.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <LedgerBody key={path} tab={tab} path={path} />
    </RequireCaps>
  );
}

function LedgerBody({ tab, path }: { tab: string; path: string }) {
  const q = useLoad<unknown>(path, [path]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  if (!q.data) return <Empty>データがありません</Empty>;
  if (tab === "sales") return <SalesView data={q.data as SalesBook} />;
  if (tab === "receivables") return <ReceivablesView data={q.data as ReceivablesBook} />;
  if (tab === "payables") return <PayablesView data={q.data as PayablesBook} />;
  if (tab === "staff_payments") return <StaffView data={q.data as StaffPaymentsBook} />;
  if (tab === "trial_balance") return <TrialView data={q.data as TrialBalance} />;
  if (tab === "purchases" || tab === "expenses") return <ExpenseView data={q.data as LedgerResponse} />;
  return <BalanceLedger data={q.data as LedgerResponse} kind={tab} />;
}

function BalanceLedger({ data, kind }: { data: LedgerResponse; kind: string }) {
  const inKey = kind === "general" ? "debit" : "in";
  const outKey = kind === "general" ? "credit" : "out";
  const cols: Column<LedgerRow>[] = useMemo(
    () => [
      { key: "date", label: "日付", render: (r) => fmtDate(r.date), width: 110 },
      { key: "description", label: "摘要", render: (r) => <span>{r.description}{r.memo ? <span style={{ color: "var(--color-muted)" }}> {r.memo}</span> : null}</span> },
      { key: "counter_accounts", label: "相手科目" },
      { key: "store", label: "店舗" },
      { key: inKey, label: kind === "general" ? "借方" : "入金", align: "right", render: (r) => (Number(r[inKey as "in"]) ? yen(r[inKey as "in"]) : "") },
      { key: outKey, label: kind === "general" ? "貸方" : "出金", align: "right", render: (r) => (Number(r[outKey as "out"]) ? yen(r[outKey as "out"]) : "") },
      { key: "balance", label: "残高", align: "right", render: (r) => yen(r.balance) },
    ],
    [inKey, outKey, kind],
  );
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="期首残高" value={yen(data.opening)} />
        <Stat label={kind === "general" ? "借方合計" : "入金合計"} value={yen(data.in_total ?? data.debit_total)} />
        <Stat label={kind === "general" ? "貸方合計" : "出金合計"} value={yen(data.out_total ?? data.credit_total)} />
        <Stat label="期末残高" value={yen(data.closing)} accent={Number(data.closing) < 0 ? "danger" : undefined} />
      </div>
      {data.account && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          科目: {data.account.name}（{data.account.debit_normal ? "借方が増加" : "貸方が増加"}）
        </p>
      )}
      <DataTable columns={cols} rows={data.rows} rowKey={(r) => r.line_id} emptyText="この期間の記録はありません。" />
    </div>
  );
}

function ExpenseView({ data }: { data: LedgerResponse }) {
  const cols: Column<LedgerRow>[] = [
    { key: "date", label: "日付", render: (r) => fmtDate(r.date), width: 110 },
    { key: "account", label: "科目" },
    { key: "description", label: "摘要", render: (r) => <span>{r.description}{r.memo ? <span style={{ color: "var(--color-muted)" }}> {r.memo}</span> : null}</span> },
    { key: "counterparty", label: "取引先" },
    { key: "store", label: "店舗" },
    { key: "tax_label", label: "税区分" },
    { key: "counter_accounts", label: "支払方法（相手科目）" },
    { key: "amount", label: "金額", align: "right", render: (r) => yen(r.amount) },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="合計" value={yen(data.total)} />
        {(data.by_account ?? []).slice(0, 3).map((a) => (
          <Stat key={a.key} label={a.name} value={yen(a.amount)} />
        ))}
      </div>
      <DataTable columns={cols} rows={data.rows} rowKey={(r) => r.line_id} />
    </div>
  );
}

function SalesView({ data }: { data: SalesBook }) {
  const t = data.totals;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="売上（税込）" value={yen(t.sales_total)} />
        <Stat label="現金" value={yen(t.cash)} />
        <Stat label="カード" value={yen(t.card)} />
        <Stat label="返金" value={yen(t.refunds)} />
        <Stat label="仕訳の売上" value={yen(t.journal_sales)} accent={t.journal_sales !== t.net_sales ? "warn" : "ok"} sub={t.journal_sales !== t.net_sales ? "締めていない日があるか、仕訳が未作成です" : "一致"} />
      </div>
      <DataTable
        columns={[
          { key: "period", label: "日付" },
          { key: "store", label: "店舗" },
          { key: "checkouts", label: "会計数", align: "right" },
          { key: "sales_total", label: "売上（税込）", align: "right", render: (r) => yen(r.sales_total) },
          { key: "tax_10", label: "10%対象", align: "right", render: (r) => yen(r.tax_10) },
          { key: "tax_8", label: "8%対象", align: "right", render: (r) => yen(r.tax_8) },
          { key: "cash", label: "現金", align: "right", render: (r) => yen(r.cash) },
          { key: "card", label: "カード", align: "right", render: (r) => yen(r.card) },
          { key: "receivable", label: "ツケ", align: "right", render: (r) => yen(r.receivable) },
          { key: "refunds", label: "返金", align: "right", render: (r) => yen(r.refunds) },
          { key: "journal_sales", label: "仕訳の売上", align: "right", render: (r) => yen(r.journal_sales) },
        ]}
        rows={data.rows}
        rowKey={(r) => `${r.period}-${r.store_id}`}
      />
    </div>
  );
}

function ReceivablesView({ data }: { data: ReceivablesBook }) {
  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-bold mb-2">カード売掛（売上 − 入金 ＝ 未入金残高）</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
          <Stat label="期首の未入金" value={yen(data.card.opening)} />
          <Stat label="期末の未入金" value={yen(data.card.closing)} accent={Number(data.card.closing) > 0 ? "warn" : "ok"} />
          <Stat label="ツケの残高" value={yen(data.tsuke_total)} accent={Number(data.tsuke_total) > 0 ? "warn" : undefined} />
        </div>
        <DataTable
          columns={[
            { key: "month", label: "月" },
            { key: "card_sales", label: "カード売上", align: "right", render: (r) => yen(r.card_sales) },
            { key: "settled", label: "入金（消込）", align: "right", render: (r) => yen(r.settled) },
            { key: "balance", label: "未入金残高", align: "right", render: (r) => yen(r.balance) },
          ]}
          rows={data.card.rows}
          rowKey={(r) => r.month}
        />
      </div>
      <div className="card">
        <h2 className="text-lg font-bold mb-2">カード入金の記録</h2>
        <DataTable
          columns={[
            { key: "deposit_date", label: "入金日", render: (r) => fmtDate(r.deposit_date) },
            { key: "provider", label: "決済会社" },
            { key: "store", label: "店舗" },
            { key: "sales_amount", label: "対象売上", align: "right", render: (r) => yen(r.sales_amount) },
            { key: "fee_amount", label: "手数料", align: "right", render: (r) => yen(r.fee_amount) },
            { key: "deposit_amount", label: "入金額", align: "right", render: (r) => yen(r.deposit_amount) },
          ]}
          rows={data.card.settlements}
          rowKey={(r) => r.id}
        />
      </div>
      <div className="card">
        <h2 className="text-lg font-bold mb-2">ツケ（未収）</h2>
        <DataTable
          columns={[
            { key: "business_date", label: "日付", render: (r) => fmtDate(r.business_date) },
            { key: "store", label: "店舗" },
            { key: "debtor", label: "お客様" },
            { key: "amount", label: "金額", align: "right", render: (r) => yen(r.amount) },
            { key: "collected", label: "回収済", align: "right", render: (r) => yen(r.collected) },
            { key: "balance", label: "残高", align: "right", render: (r) => yen(r.balance) },
          ]}
          rows={data.tsuke}
          rowKey={(r) => r.id}
          emptyText="未収はありません。"
        />
      </div>
    </div>
  );
}

function PayablesView({ data }: { data: PayablesBook }) {
  return (
    <div className="space-y-3">
      <Stat label="未払の残高" value={yen(data.open_total)} accent={Number(data.open_total) > 0 ? "warn" : "ok"} />
      <DataTable
        columns={[
          { key: "invoice_date", label: "請求日", render: (r) => fmtDate(r.invoice_date) },
          { key: "due_date", label: "支払期日", render: (r) => (r.due_date ? fmtDate(r.due_date) : "") },
          { key: "counterparty", label: "取引先" },
          { key: "account", label: "科目" },
          { key: "description", label: "内容" },
          { key: "amount", label: "金額", align: "right", render: (r) => yen(r.amount) },
          { key: "paid", label: "支払済", align: "right", render: (r) => yen(r.paid) },
          { key: "balance", label: "残高", align: "right", render: (r) => yen(r.balance) },
          { key: "status", label: "状態" },
        ]}
        rows={data.rows}
        rowKey={(r) => r.id}
        emptyText="買掛金・未払金はありません。"
      />
    </div>
  );
}

function StaffView({ data }: { data: StaffPaymentsBook }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="報酬総額" value={yen(data.totals.gross)} />
        <Stat label="源泉所得税" value={yen(data.totals.withholding)} />
        <Stat label="その他控除" value={yen(data.totals.deduction)} />
        <Stat label="手渡し・振込額" value={yen(data.totals.net)} />
      </div>
      <div className="card">
        <h2 className="text-lg font-bold mb-2">スタッフ別（年 50 万円超は支払調書の対象になり得ます）</h2>
        <DataTable
          columns={[
            { key: "employee", label: "スタッフ" },
            { key: "count", label: "件数", align: "right" },
            { key: "gross", label: "報酬総額", align: "right", render: (r) => yen(r.gross) },
            { key: "withholding", label: "源泉所得税", align: "right", render: (r) => yen(r.withholding) },
            { key: "net", label: "支払額", align: "right", render: (r) => yen(r.net) },
            { key: "over_500k", label: "", render: (r) => (r.over_500k ? <span className="badge badge-warn">50万円超</span> : null) },
          ]}
          rows={data.by_employee}
          rowKey={(r) => r.employee_id}
        />
      </div>
      <DataTable
        columns={[
          { key: "date", label: "営業日", render: (r) => fmtDate(r.date) },
          { key: "store", label: "店舗" },
          { key: "employee", label: "スタッフ" },
          { key: "kind", label: "種別" },
          { key: "method", label: "方法" },
          { key: "gross", label: "報酬総額", align: "right", render: (r) => yen(r.gross) },
          { key: "withholding", label: "源泉", align: "right", render: (r) => yen(r.withholding) },
          { key: "deduction", label: "控除", align: "right", render: (r) => yen(r.deduction) },
          { key: "net", label: "支払額", align: "right", render: (r) => yen(r.net) },
          { key: "is_voided", label: "", render: (r) => (r.is_voided ? <span className="badge badge-danger">取消</span> : null) },
        ]}
        rows={data.rows}
        rowKey={(r) => `${r.paid_at}-${r.employee_id}`}
        rowClassName={(r) => (r.is_voided ? "opacity-50" : "")}
      />
    </div>
  );
}

function TrialView({ data }: { data: TrialBalance }) {
  return (
    <DataTable
      columns={[
        { key: "name", label: "科目" },
        { key: "category", label: "区分" },
        { key: "debit", label: "借方合計", align: "right", render: (r) => yen(r.debit) },
        { key: "credit", label: "貸方合計", align: "right", render: (r) => yen(r.credit) },
        { key: "balance", label: "残高", align: "right", render: (r) => yen(r.balance) },
      ]}
      rows={data.rows}
      rowKey={(r) => r.key}
    />
  );
}
