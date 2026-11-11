/** 帳簿基盤（勘定科目・年度・帳簿）の API 型。金額は文字列の Decimal。 */
import type { Money } from "./types";

export type Account = {
  id: number;
  key: string;
  code: string;
  name: string;
  category: "asset" | "liability" | "equity" | "owner" | "revenue" | "cogs" | "expense";
  category_label: string;
  statement_line: string;
  statement_line_label: string;
  default_tax: string;
  default_tax_label: string;
  is_cash: boolean;
  is_bank: boolean;
  is_system: boolean;
  is_active: boolean;
  sort_order: number;
  note: string;
};

export type AccountsResponse = {
  accounts: Account[];
  statement_lines: Record<string, string>;
  tax_categories: Record<string, string>;
  categories: Record<string, string>;
};

export type FiscalYear = {
  id: number;
  year: number;
  start_date: string;
  end_date: string;
  filing_type: "white" | "blue_10" | "blue_55" | "blue_65";
  status: "open" | "closing" | "closed" | "filed";
  status_label: string;
  closed_at: string | null;
  filed_at: string | null;
  reopen_count: number;
  drift_warnings: { sync_key: string; detected_at: string; message: string }[];
  note: string;
  is_locked: boolean;
  events: { action: string; reason: string; by: string; at: string }[];
  checklist?: { code: string; label: string; count: number; blocking: boolean }[];
};

export type LedgerRow = {
  date: string;
  journal_id: number;
  line_id: number;
  description: string;
  memo: string;
  counter_accounts: string;
  debit?: Money;
  credit?: Money;
  in?: Money;
  out?: Money;
  balance?: Money;
  amount?: Money;
  tax_category: string;
  tax_label: string;
  store: string;
  store_id: number | null;
  counterparty: string;
  employee: string;
  entry_kind: string;
  source_type: string;
  account?: string;
  account_key?: string;
};

export type LedgerResponse = {
  from: string;
  to: string;
  rows: LedgerRow[];
  opening?: Money;
  closing?: Money;
  in_total?: Money;
  out_total?: Money;
  debit_total?: Money;
  credit_total?: Money;
  total?: Money;
  account?: { key: string; name: string; category: string; debit_normal: boolean } | null;
  by_account?: { key: string; name: string; statement_line: string; amount: Money }[];
};

export type SalesBookRow = {
  period: string;
  store_id: number;
  store: string;
  checkouts: number;
  sales_total: Money;
  tax_10: Money;
  tax_8: Money;
  other_rate: Money;
  cash: Money;
  card: Money;
  receivable: Money;
  refunds: Money;
  net_sales: Money;
  journal_sales: Money;
};

export type SalesBook = { from: string; to: string; group: string; rows: SalesBookRow[]; totals: Record<string, Money> };

export type ReceivablesBook = {
  card: { opening: Money; closing: Money; rows: { month: string; card_sales: Money; settled: Money; balance: Money }[]; settlements: { id: number; deposit_date: string; provider: string; sales_amount: Money; fee_amount: Money; deposit_amount: Money; store: string }[] };
  tsuke: { id: number; store: string; business_date: string; debtor: string; amount: Money; collected: Money; balance: Money; status: string }[];
  tsuke_total: Money;
};

export type PayablesBook = {
  rows: { id: number; invoice_date: string; due_date: string | null; counterparty: string; store: string; amount: Money; paid: Money; balance: Money; status: string; description: string; account: string }[];
  open_total: Money;
};

export type StaffPaymentsBook = {
  rows: { date: string; paid_at: string; store: string; employee_id: number; employee: string; kind: string; method: string; gross: Money; withholding: Money; deduction: Money; net: Money; tax_category: string; is_voided: boolean; status: string }[];
  totals: Record<string, Money>;
  by_employee: { employee_id: number; employee: string; gross: Money; withholding: Money; net: Money; count: number; over_500k: boolean }[];
};

export type TrialBalance = { rows: { key: string; name: string; category: string; debit: Money; credit: Money; balance: Money }[] };

export type ProfitLoss = {
  from: string;
  to: string;
  income: { key: string; name: string; amount: Money }[];
  cogs: { key: string; name: string; amount: Money }[];
  expense: { key: string; name: string; amount: Money }[];
  by_statement_line: { line: string; label: string; amount: Money }[];
  by_store: { store_id: number; store: string; income: Money; expense: Money; profit: Money }[];
  total_income: Money;
  total_cogs: Money;
  total_expense: Money;
  gross_profit: Money;
  profit: Money;
  suspense_total: Money;
  notice: string;
};

export type JournalLineDetail = {
  line_no: number;
  side: "debit" | "credit";
  account_key: string;
  account_name: string;
  amount: Money;
  tax_category: string;
  tax_amount: Money | null;
  store: string;
  counterparty: string;
  employee: string;
  memo: string;
};
