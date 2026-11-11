/** 確定申告 API の型。金額は文字列。 */
import type { Money } from "./types";
import type { FiscalYear } from "./types_books";

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

export type TaxInputs = {
  social_insurance?: Money;
  small_business_mutual?: Money;
  donation?: Money;
  withheld_tax?: Money;
  estimated_tax_paid?: Money;
  loss_carryforward?: Money;
  family_employee?: Money;
  housing_loan_credit?: Money;
  casualty_loss?: Money;
  salary?: { revenue: Money; withheld: Money; social_insurance: Money };
  other_incomes?: { kind: string; amount: Money; label: string }[];
  life_insurance?: { new_general: Money; old_general: Money; care: Money; new_pension: Money; old_pension: Money; has_under23: boolean };
  earthquake_insurance?: { earthquake: Money; old_long_term: Money };
  medical?: { paid: Money; reimbursed: Money };
  spouse?: { has: boolean; income: Money; age70: boolean };
  dependents?: { general: number; specific: number; elderly: number; elderly_cohabiting: number; specific_relatives: { income: Money }[] };
  personal?: { disability_general: number; disability_special: number; disability_special_cohabiting: number; widow: boolean; single_parent: boolean; working_student: boolean };
  is_partial_year_return?: boolean;
  memo?: string;
};

export type TraceRow = { key: string; label: string; value: Money; basis: string; detail?: unknown };
export type DeductionRow = { key: string; label: string; amount: Money; basis: string; kind?: string; detail?: unknown };
export type StatementRow = { no: string; key: string; label: string; amount: Money; custom?: boolean };

export type TaxComputed = {
  computed_at: string;
  config_year: number;
  config_hash: string;
  filing_type_applied: string;
  statement_rows: StatementRow[];
  deductions: DeductionRow[];
  trace: TraceRow[];
  values: Record<string, Money>;
  notice: string;
};

export type TaxReturnData = {
  id: number;
  year: number;
  filing_type: "white" | "blue_10";
  status: string;
  status_label: string;
  version: number;
  inputs: TaxInputs;
  business_summary: { values: Record<string, Money>; custom_rows: { key: string; name: string; amount: Money }[]; by_store: { store: string; income: Money; expense: Money; profit: Money }[]; monthly: { month: string; sales: Money; purchases: Money }[]; has_suspense: boolean } | Record<string, never>;
  computed: TaxComputed | Record<string, never>;
  unsupported_items: { code: string; message: string }[];
  warnings: string[];
  completed_at: string | null;
  filed_at: string | null;
  filed_note: string;
  fiscal_year: FiscalYear;
  config: { year: number; status: string; content_hash: string };
  profile: TaxProfile | null;
  versions: { id: number; version_no: number; created_at: string; created_by: string; reason: string; is_final: boolean; tax_due: Money | null; refund: Money | null; unsupported: number }[];
  xtx_available: boolean;
  xtx_released: boolean;
  version_created?: { id: number; version_no: number };
};

export type GuideItem = {
  section: string;
  order: number;
  key: string;
  label: string;
  source: string | null;
  filing: "both" | "white" | "blue";
  help?: string;
  copyable: boolean;
  value: unknown;
  official_label: string | null;
  verified: boolean;
  checked: boolean;
  checked_at: string | null;
  copied_at: string | null;
  note: string;
};

export type Guide = {
  year: number;
  filing: "white" | "blue";
  basis: string;
  sources: { title: string; url: string }[];
  links: { label: string; url: string }[];
  sections: { key: string; label: string; intro: string }[];
  items: GuideItem[];
  progress: { done: number; total: number };
  unsupported: { code: string; message: string }[];
  warnings: string[];
  status: string;
  computed_at: string | null;
  all_verified: boolean;
  notice: string;
};

export type TaxExportRow = {
  id: number;
  format: string;
  filename: string;
  sha256: string;
  size: number;
  generated_at: string;
  version_no: number;
  is_released: boolean;
  validation: Record<string, unknown>;
  download_count: number;
};
