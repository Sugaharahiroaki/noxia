/** 管理用の集計 API（/api/reports/...）の型。金額はサーバーで確定した文字列。 */
import type { Money } from "./types";

export type ReportSettings = { cast_sales_split_mode: "equal" | "full_each"; nomination_count_mode: "per_slip" | "per_set"; withholding_enabled: boolean };

export type SlipRow = {
  id: number;
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
    paid: boolean;
  }[];
  checkouts: {
    id: number;
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
    douhan_count: string;
  }[];
  settings: ReportSettings;
};
