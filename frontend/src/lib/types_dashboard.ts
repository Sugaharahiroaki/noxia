/** 会計ダッシュボード・ファイル出力・会計設定で使う型。金額は文字列の Decimal。 */
import type { AccountingOverview, Money } from "./types";
import type { FiscalYear, ProfitLoss } from "./types_books";

export type OverviewCounts = {
  pending_receipts: number;
  returned_receipts: number;
  unmatched_bank: number;
  open_days: number;
  suspense_journals: number;
  unassigned_cash_expenses: number;
  fiscal_years: FiscalYear[];
};

export type MonthPl = {
  from: string;
  to: string;
  total_income: Money;
  total_expense: Money;
  profit: Money;
  suspense_total: Money;
};

export type AccountingOverviewEx = AccountingOverview & {
  counts?: Partial<OverviewCounts>;
  month_pl?: MonthPl | null;
};

export type { ProfitLoss };

export type TaxProfile = {
  individual_name: string;
  trade_name: string;
  business_start_date: string | null;
  blue_approval_status: "none" | "applied" | "approved" | "revoked";
  blue_approved_from_year: number | null;
  blue_application_date: string | null;
  bookkeeping_method: "simple" | "double";
  consumption_tax_status: "exempt" | "taxable_general" | "taxable_simplified" | "taxable_2wari";
  invoice_registration_number: string;
  note: string;
};

export type TaxProfileResponse = { taxpayer_id: number; entity_type: "corporation" | "sole_proprietor"; profile: TaxProfile | null };
