/** 年間決算（固定資産・減価償却・棚卸・決算整理・年間損益）の API 型。金額は文字列の Decimal。 */
import type { Money } from "./types";
import type { FiscalYear, ProfitLoss } from "./types_books";

export type DepreciationMethodKey = "straight_line" | "declining_balance" | "lump_sum_3yr" | "immediate" | "blue_small_asset" | "voluntary";

export type MethodOption = { key: DepreciationMethodKey; label: string; help: string; available: boolean };

export type FixedAsset = {
  id: number;
  name: string;
  kind: "tangible" | "intangible" | "deferred_opening_cost" | "deferred_other";
  kind_label: string;
  category: string;
  acquired_on: string;
  acquisition_cost: Money;
  business_use_percent: Money;
  depreciation_method: DepreciationMethodKey;
  method_label: string;
  useful_life_years: number | null;
  rate: string | null;
  memorandum_value: Money;
  status: "in_use" | "disposed" | "sold";
  status_label: string;
  disposed_on: string | null;
  disposal_amount: Money | null;
  paid_from: string;
  evidence: number | null;
  store: number | null;
  store_name: string;
  note: string;
  accumulated: Money;
  book_value: Money;
  years: { year: number; amount_total: Money }[];
  created_at: string;
  entries?: DepreciationEntry[];
};

export type DepreciationEntry = {
  id: number;
  asset_id: number;
  asset_name: string;
  category: string;
  kind: string;
  method: DepreciationMethodKey;
  method_label: string;
  acquired_on: string;
  acquisition_cost: Money;
  useful_life_years: number | null;
  rate: string | null;
  business_use_percent: Money;
  months_in_service: number;
  amount_total: Money;
  amount_business: Money;
  amount_household: Money;
  book_value_begin: Money;
  book_value_end: Money;
  requested_amount: Money | null;
  calc_basis: Record<string, unknown>;
  journal_id: number | null;
  store: string;
};

export type AssetsResponse = { assets: FixedAsset[]; methods: MethodOption[]; rates: { rates: Record<string, string>; source: string; source_url: string } };
export type DepreciationResponse = { year: number; entries: DepreciationEntry[]; total_business: Money; locked: boolean };

export type InventoryLine = { id: number; product: number | null; name: string; quantity: string; unit_cost: Money; amount: Money; cost_source: string };
export type InventoryClosing = {
  id: number;
  year: number;
  store: number;
  store_name: string;
  kind: "opening" | "closing";
  kind_label: string;
  as_of: string;
  method: string;
  method_label: string;
  total_amount: Money;
  stock_take: number | null;
  status: "draft" | "confirmed";
  confirmed_at: string | null;
  journal_id: number | null;
  note: string;
  unpriced_count: number;
  lines?: InventoryLine[];
};
export type InventoryResponse = {
  closings: InventoryClosing[];
  stock_takes: { id: number; store: number; store_name: string; taken_at: string; status: string; lines: number }[];
};

export type AdjustmentKind = { key: string; label: string; help: string; default_debit: string; default_credit: string };
export type ClosingAdjustment = {
  id: number;
  year: number;
  kind: string;
  kind_label: string;
  issue_date: string;
  description: string;
  amount: Money;
  debit_account: { key: string; name: string };
  credit_account: { key: string; name: string };
  store: number | null;
  store_name: string;
  journal_id: number | null;
  is_voided: boolean;
  void_reason: string;
  created_at: string;
};
export type AdjustmentsResponse = { adjustments: ClosingAdjustment[]; kinds: AdjustmentKind[] };

export type ChecklistResponse = {
  year: number;
  fiscal_year: FiscalYear;
  items: { code: string; label: string; count: number; blocking: boolean }[];
  methods: MethodOption[];
};

export type YearPl = ProfitLoss & {
  year: number;
  filing_type: string;
  status: string;
  months: string[];
  monthly_rows: { line: string; label: string; months: Money[]; total: Money }[];
  month_summary: { month: string; income: Money; cogs: Money; expense: Money; profit: Money }[];
  adjustments: ClosingAdjustment[];
  depreciation: DepreciationEntry[];
  depreciation_total: Money;
};
