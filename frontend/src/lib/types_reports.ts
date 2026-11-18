/** 管理用の集計 API（/api/reports/...）の型。金額はサーバーで確定した文字列。 */
import type { Money } from "./types";

export type ReportSettings = { cast_sales_split_mode: "equal" | "full_each"; nomination_count_mode: "per_slip" | "per_set"; withholding_enabled: boolean };

export type SlipRow = {
  id: number;
  /** 伝票 ID（連番にしないランダムな英数字 12 文字。表示は 4 文字区切り） */
  public_id: string;
  slip_number: number;
  business_date: string;
  status: "open" | "closed" | "cancelled";
  status_label: string;
  table_name: string;
  opened_at: string;
  closed_at: string | null;
  minutes: number | null;
  guest_count: number;
  customer: string;
  tags: { name: string; color: string }[];
  nomination: string[];
  jounai: string[];
  yobi: string[];
  douhan: string[];
  subtotal: Money;
  service_charge: Money;
  discount: Money;
  cash: Money;
  card: Money;
  receivable: Money;
  total: Money;
  refunds: Money;
  checkout_count: number;
  voided_checkouts: number;
  cancel_reason: string;
};

export type SlipList = {
  from: string;
  to: string;
  rows: SlipRow[];
  totals: { subtotal: Money; service_charge: Money; discount: Money; cash: Money; card: Money; receivable: Money; total: Money; refunds: Money; count: number; guest_count: number };
  settings: ReportSettings;
};

export type SlipDetail = SlipRow & {
  note: string;
  present_count: number;
  courses: string[];
  tax: Money;
  tendered: Money;
  change: Money;
  set_count: number;
  lines: {
    id: number;
    line_type: string;
    line_type_label: string;
    name: string;
    cast: string[];
    unit_price: Money;
    tax_rate: Money;
    tax_included: boolean;
    quantity: Money;
    amount: Money;
    status: "active" | "cancelled";
    cancel_reason: string;
    checkout_number: number | null;
    checkout_public_id: string | null;
    paid: boolean;
  }[];
  checkouts: {
    id: number;
    public_id: string;
    number: number;
    kind: string;
    status: string;
    status_label: string;
    confirmed_at: string;
    subtotal: Money;
    discount_total: Money;
    service_charge: Money;
    tax_total: Money;
    rounding_adjustment: Money;
    total: Money;
    payments: { method: string; method_label: string; amount: Money; tendered: Money | null; change_amount: Money; status: string }[];
    refunds: { amount: Money; method: string; reason: string }[];
  }[];
  cast_sales: {
    employee_id: number;
    employee_name: string;
    yobi_subtotal: Money;
    yobi_total: Money;
    douhan_subtotal: Money;
    douhan_total: Money;
    nomination_count: string;
    jounai_count: string;
    yobi_count: string;
    douhan_count: string;
  }[];
  settings: ReportSettings;
};

/** 収支の 1 日ぶん（または合計）。式はサーバーの profit_metrics */
export type IncomeDay = {
  date: string;
  sales: Money;
  cash: Money;
  card: Money;
  receivable: Money;
  cast_sales: Money;
  yobi_subtotal: Money;
  douhan_subtotal: Money;
  labor_cast: Money;
  labor_staff: Money;
  labor_total: Money;
  income: Money;
  expenses: Money;
  cost_total: Money;
  daily_pay_cast: Money;
  daily_pay_staff: Money;
  bonus: Money;
  minus: Money;
  refunds: Money;
  gross_profit: Money;
  gross_profit_cumulative?: Money;
  pay_ratio: string | null;
  per_guest: Money | null;
  per_group: Money | null;
  guests: number;
  groups: number;
  yobi_groups: number;
  checkouts: number;
  nomination_count: string;
  has_slips: boolean;
};

export type MonthlyReport = {
  month: string;
  from: string;
  to: string;
  rows: IncomeDay[];
  totals: IncomeDay;
  active_days: number;
  average: Record<"sales" | "cast_sales" | "labor_cast" | "gross_profit", Money | null>;
  profit_kind_note: string;
  settings: ReportSettings;
};

export type CashDayRow = {
  date: string;
  status: "open" | "closed";
  status_label: string;
  opening_float: Money;
  cash_sales: Money;
  cash_in: Money;
  cash_out: Money;
  daily_pay: Money;
  theoretical_cash: Money;
  counted_cash: Money | null;
  difference: Money | null;
  set_aside_amount: Money | null;
  next_day_float: Money | null;
  difference_reason: string;
};

export type CashMonth = {
  month: string;
  rows: CashDayRow[];
  totals: { cash_sales: Money; cash_in: Money; cash_out: Money; difference: Money; set_aside_amount: Money };
  formula_note: string;
};

export type DailyReport = {
  date: string;
  today: IncomeDay;
  cumulative: IncomeDay;
  active_days: number;
  average: Record<"sales" | "cash" | "card" | "receivable" | "cast_sales" | "labor_cast" | "gross_profit", Money | null>;
  cash: CashDayRow | null;
  cash_month: { difference: Money; set_aside: Money };
  movements: { id: number; kind: string; kind_label: string; direction: "in" | "out"; amount: Money; reason: string; employee_name: string; occurred_at: string; is_voided: boolean; in_profit: boolean; item_name?: string }[];
  slips: SlipRow[];
  slip_totals: SlipList["totals"];
  profit_kind_note: string;
  cash_formula_note: string;
  settings: ReportSettings;
};