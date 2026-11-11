/** 銀行（口座・CSV 取込・明細・照合）の API 型。金額は文字列の Decimal。 */
import type { Money } from "./types";

export type BankAccount = {
  id: number;
  name: string;
  bank_name: string;
  bank_code: string;
  branch_name: string;
  branch_code: string;
  account_type: "ordinary" | "checking" | "savings";
  account_type_label: string;
  account_number_last4: string;
  holder_name: string;
  opening_balance: Money;
  opening_balance_date: string | null;
  is_business: boolean;
  is_active: boolean;
  note: string;
};

export type ColumnMap = Record<string, number | null>;

export type ImportPreview = {
  encoding: string;
  delimiter: string;
  header_row: number | null;
  header: string[];
  column_map: ColumnMap;
  guessed: boolean;
  profile_id: number | null;
  descending: boolean;
  preview: string[][];
  columns: { key: string; label: string }[];
  rows_total: number;
  rows_parsed: number;
  rows_duplicate: number;
  errors: { row: number | null; message: string }[];
  period_start: string | null;
  period_end: string | null;
  balance_check: "ok" | "mismatch" | "unknown";
  balance_note: string;
  opening_balance: Money | null;
  closing_balance: Money | null;
  sample: { row_no: number; date: string; description: string; counterparty: string; amount_in: Money; amount_out: Money; balance: Money | null; duplicate: boolean }[];
};

export type BankImport = {
  id: number;
  bank_account: number;
  bank_account_name: string;
  profile: number | null;
  original_name: string;
  encoding_detected: string;
  rows_total: number;
  rows_imported: number;
  rows_duplicate: number;
  rows_error: number;
  period_start: string | null;
  period_end: string | null;
  opening_balance: Money | null;
  closing_balance: Money | null;
  balance_check: "ok" | "mismatch" | "unknown";
  balance_note: string;
  status: "parsed" | "committed" | "rolled_back";
  status_label: string;
  error_log: { row: number | null; message: string }[];
  imported_by_name: string;
  committed_at: string | null;
  created_at: string;
};

export type Reconciliation = {
  id: number;
  bank_transaction: number;
  match_type: string;
  match_type_label: string;
  target_id: number | null;
  counter_account: string | null;
  journal: number | null;
  amount: Money;
  fee_amount: Money;
  status: "suggested" | "confirmed" | "voided";
  status_label: string;
  rule: string;
  score: Money;
  label: string;
  period_start: string | null;
  period_end: string | null;
  store_name: string | null;
  confirmed_at: string | null;
  voided_at: string | null;
  void_reason: string;
};

export type BankTransaction = {
  id: number;
  bank_account: number;
  bank_account_name: string;
  row_no: number;
  date: string;
  description: string;
  counterparty: string;
  amount_in: Money;
  amount_out: Money;
  balance: Money | null;
  status: "unmatched" | "suggested" | "matched" | "partially_matched" | "ignored" | "transfer";
  status_label: string;
  ignore_reason: string;
  reconciliations: Reconciliation[];
};

export type Candidate = {
  match_type: string;
  target_id: number | null;
  amount: Money;
  fee_amount: Money;
  rule: string;
  score: number;
  label: string;
  store_id?: number;
  period_start?: string;
  period_end?: string;
  items?: { target_id: number; amount: Money }[];
};

export type BankSummary = {
  from: string;
  to: string;
  by_status: Record<string, number>;
  unmatched_count: number;
  unmatched_in: Money;
  unmatched_out: Money;
  accounts: { id: number; name: string; statement_balance: Money | null; statement_balance_date: string | null; book_balance: Money; unmatched: number }[];
};
