/** 領収書・経費登録（apps.expenses）の API 型。金額は文字列の Decimal。 */
import type { Money } from "./types";

export type ReceiptStatus = "draft" | "ocr_running" | "pending" | "approved" | "returned" | "rejected" | "voided";
export type ReceiptPaymentMethod = "store_cash" | "owner_personal" | "bank" | "card" | "other";
export type TaxBreakdownRow = { rate: string; taxable_incl: string; tax: string | null };

export type OcrInfo = {
  status: "none" | "running" | "done" | "failed" | "skipped";
  status_label: string;
  provider: string;
  model: string;
  error: string;
  date: string | null;
  vendor: string;
  total: Money | null;
  tax_breakdown: TaxBreakdownRow[];
  invoice_number: string;
  summary: string;
  missing_fields: string[];
  warnings: { code: string; message: string }[];
  is_receipt: boolean;
  receipt_count: number;
  suggested_accounts?: { key: string; reason: string }[];
  suggested_account_key?: string;
  cost_jpy?: Money;
};

/** スタッフ向け（科目・税区分・承認メモは含まれない） */
export type Receipt = {
  id: number;
  status: ReceiptStatus;
  status_label: string;
  payment_method: ReceiptPaymentMethod;
  payment_method_label: string;
  store: number;
  store_name: string;
  file_name: string;
  content_type: string;
  quality_flags: string[];
  receipt_date: string | null;
  vendor: string;
  total_amount: Money | null;
  tax_breakdown: TaxBreakdownRow[];
  invoice_number: string;
  summary: string;
  memo: string;
  ocr: OcrInfo;
  return_reason: string;
  reject_reason: string;
  submitted_at: string | null;
  submitted_by_name: string;
  reviewed_at: string | null;
  version: number;
  can_edit: boolean;
  has_duplicate_warning: boolean;
  created_at: string;
  poll_after_ms?: number;
};

export type DuplicateCandidate = { receipt_id: number; reasons: string[]; status: string; date: string | null; amount: Money | null; vendor: string; store_id: number; distance?: number; similarity?: number | null };
export type CashMovementCandidate = { cash_movement_id: number; amount: Money; reason: string; occurred_at: string; amount_match: boolean; business_day_status: string };

/** 承認者向け（全項目） */
export type ReceiptFull = Receipt & {
  taxpayer_id: number | null;
  evidence_id: number;
  sha256: string;
  origin: string;
  approved_date: string | null;
  approved_vendor: string;
  approved_amount: Money | null;
  approved_tax_breakdown: TaxBreakdownRow[];
  account: number | null;
  account_key: string | null;
  account_name: string | null;
  approved_tax_category: string;
  approved_store: number | null;
  approved_store_name: string | null;
  approved_payment_method: string;
  approved_note: string;
  duplicate_candidates: DuplicateCandidate[];
  duplicate_override_reason: string;
  cash_movement: number | null;
  expense: number | null;
  payable: number | null;
  reviewed_by_name: string;
  corrected_from: number | null;
  cash_movement_candidates?: CashMovementCandidate[];
};

export type ReceiptList<T = Receipt> = { items: T[]; total: number; page: number; page_size: number };
export type PendingCounts = Record<ReceiptStatus, number> & { duplicates: number; ocr_failed: number };
export type ReceiptEvent = { id: number; action: string; actor_name: string; at: string; before: Record<string, unknown>; after: Record<string, unknown>; reason: string };

export type PayablePayment = { id: number; payable: number; date: string; amount: Money; method: string; cash_movement: number | null; bank_transaction_ref: number | null; note: string; is_voided: boolean; void_reason: string; created_at: string };
export type Payable = {
  id: number;
  store: number | null;
  store_name: string | null;
  supplier: number | null;
  supplier_name: string | null;
  counterparty: string;
  invoice_date: string;
  due_date: string | null;
  amount: Money;
  tax_rate: Money;
  tax_breakdown: TaxBreakdownRow[];
  account: number | null;
  account_key: string | null;
  account_name: string | null;
  description: string;
  evidence: number | null;
  source: string;
  status: "open" | "partially_paid" | "paid" | "voided";
  paid_amount: Money;
  balance: Money;
  is_voided: boolean;
  void_reason: string;
  payments: PayablePayment[];
  created_at: string;
};

export type RegisterCheckDay = {
  store_id: number;
  store: string;
  date: string;
  business_day_status: string | null;
  pos_expense_total: Money;
  receipt_total: Money;
  difference: Money;
  status: "match" | "diff" | "no_data";
  matched: { cash_movement_id: number; amount: Money; reason: string; account: string; occurred_at: string; receipt_id: number }[];
  no_receipt: { cash_movement_id: number; amount: Money; reason: string; account: string; occurred_at: string }[];
  receipt_without_movement: { receipt_id: number; amount: Money; vendor: string; cash_movement_id: number | null }[];
};
export type RegisterCheckRange = { store_id: number; from: string; to: string; rows: RegisterCheckDay[]; no_receipt_count: number; diff_days: number };

export type AiSettings = { enabled: boolean; provider: string; server_provider: string; available: boolean; reason: string; budget_jpy: Money; spent_jpy: Money; model: string };

export const STATUS_LABELS: Record<ReceiptStatus, string> = {
  draft: "下書き",
  ocr_running: "AI読取中",
  pending: "確認待ち",
  approved: "承認済み",
  returned: "差戻し",
  rejected: "却下",
  voided: "取消",
};

export const STATUS_BADGE: Record<ReceiptStatus, string> = {
  draft: "badge",
  ocr_running: "badge badge-accent",
  pending: "badge badge-warn",
  approved: "badge badge-ok",
  returned: "badge badge-danger",
  rejected: "badge",
  voided: "badge",
};

export const PAYMENT_METHODS: { key: ReceiptPaymentMethod; label: string; hint: string }[] = [
  { key: "store_cash", label: "店舗現金", hint: "レジのお金で払った（レジ現金が減ります）" },
  { key: "owner_personal", label: "オーナー個人資金", hint: "オーナーが自分の財布で払った" },
  { key: "bank", label: "銀行振込", hint: "事業用口座から振り込んだ・引き落とされた" },
  { key: "card", label: "クレジットカード", hint: "事業用カードで払った（後日引落し）" },
  { key: "other", label: "その他", hint: "分からないとき（承認するときに決めます）" },
];

export const TAX_CATEGORY_LABELS: Record<string, string> = {
  purchase_10: "課税仕入10%",
  purchase_8: "課税仕入8%（軽減）",
  non_taxable: "非課税",
  out_of_scope: "対象外",
};

export const DUPLICATE_REASON_LABELS: Record<string, string> = {
  sha256: "同じファイル",
  phash: "よく似た画像",
  date_amount_vendor: "同じ日付・金額・支払先",
};
