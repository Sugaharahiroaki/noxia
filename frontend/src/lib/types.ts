/** API のレスポンス型（金額は Django 側で確定した文字列の Decimal） */

export type Money = string;

export type StoreInfo = {
  id: number;
  name: string;
  code: string;
  is_active: boolean;
  is_demo: boolean;
  company: { id: number; name: string };
  role: string;
  caps: string[];
  read_only: boolean;
  /** 店舗の事業者（会計設定で対応付けたもの）。申告方法で経理メニューを出し分ける */
  taxpayer?: { id: number; name: string; entity_type: "corporation" | "sole_proprietor"; filing_method: "" | "freee" | "nta_corner" } | null;
};

export type Me = {
  user: { id: number; username: string; display_name: string; is_platform_operator: boolean; is_active: boolean };
  stores: StoreInfo[];
  companies: { id: number; name: string; role: string; is_active: boolean }[];
  employee: { id: number; display_name: string; company_id: number } | null;
  reauth_until: number | null;
  reauth_valid: boolean;
  server_now: string;
};

export type Course = {
  id: number;
  name: string;
  price: Money;
  duration_minutes: number | null;
  extension_price: Money;
  extension_minutes: number;
  tax_rate: Money;
  is_active: boolean;
  sort_order: number;
  note: string;
  is_unlimited: boolean;
};

export type Category = { id: number; name: string; kind: string; sort_order: number; is_active: boolean };

export type Product = {
  id: number;
  category: number;
  category_name: string;
  category_kind: string;
  name: string;
  price: Money;
  tax_rate: Money;
  is_active: boolean;
  sort_order: number;
  stock_mode: "none" | "bottle" | "shot";
  volume_ml: Money | null;
  shot_ml: Money | null;
  reorder_point: Money | null;
  min_stock: Money | null;
  cost_price: Money | null;
  is_bottle_keepable: boolean;
  note: string;
};

export type Table = { id: number; name: string; capacity: number; sort_order: number; is_active: boolean };

export type StoreSettings = {
  settings_note: string;
  price_basis: "per_person" | "per_group";
  prices_include_tax: boolean;
  default_tax_rate: Money;
  service_charge_enabled: boolean;
  service_charge_rate: Money;
  service_charge_target: string;
  service_charge_order: string;
  service_charge_after_discount: boolean;
  tax_rounding: string;
  service_charge_rounding: string;
  total_rounding_unit: number;
  total_rounding_mode: string;
  unlimited_change_mode: string;
  unlimited_change_price: Money | null;
  unlimited_change_after_ext_mode: string;
  unlimited_change_after_ext_price: Money | null;
  default_unlimited_course: number | null;
  default_unlimited_course_name: string | null;
  nomination_fee: Money | null;
  douhan_fee: Money | null;
  alarm_mode: "10" | "5" | "both";
  business_day_switch_time: string;
  default_opening_float: Money;
  receivable_allowed: boolean;
  default_back_confirm_timing: string;
  /** 日払いで源泉所得税を差し引く（既定はオフ。変更は経営者のみ） */
  withholding_enabled: boolean;
  /** お渡しのときにチェックして差し引く控除（報酬総額の%） */
  payout_deductions: PayoutDeductionItem[];
};

export type PayoutDeductionItem = { code: string; label: string; percent: Money; default_on: boolean };

export type Alarm = {
  level: "warn10" | "warn5" | "overdue";
  label: string;
  remaining_seconds: number;
  planned_end_at: string;
};

export type FloorAlarm = Alarm & {
  visit_id: number;
  slip_number: number;
  table_name: string;
  entry_id: number;
  label: string;
  course_name: string;
};

export type CourseEvent = {
  id: number;
  kind: string;
  performed_by_name: string;
  performed_at: string;
  from_planned_end_at: string | null;
  to_planned_end_at: string | null;
  amount: Money | null;
  note: string;
};

export type CourseEntry = {
  id: number;
  visit: number;
  label: string;
  guest_count: number;
  course: number;
  course_name: string;
  course_price: Money;
  extension_price: Money;
  extension_minutes: number;
  started_at: string;
  planned_end_at: string | null;
  is_unlimited: boolean;
  extension_count: number;
  ended_at: string | null;
  employee: number | null;
  employee_name: string | null;
  is_active: boolean;
  alarm: Alarm | null;
  events?: CourseEvent[];
};

export type BackTarget = { employee: number; employee_name: string; share_type: "percent" | "fixed"; share_value: Money };

export type OrderLine = {
  id: number;
  visit: number;
  line_type: "course_set" | "extension" | "course_change" | "nomination" | "douhan" | "product" | "discount";
  line_type_label: string;
  course_entry: number | null;
  product: number | null;
  name: string;
  unit_price: Money;
  quantity: Money;
  amount: Money;
  tax_rate: Money;
  tax_included: boolean;
  category_kind: string;
  status: "active" | "cancelled";
  cancel_reason: string;
  cancelled_by_name: string;
  cancelled_at: string | null;
  discount_reason: string;
  checkout: number | null;
  note: string;
  back_targets: BackTarget[];
  created_at: string;
};

export type Quote = {
  subtotal: Money;
  discount_total: Money;
  service_charge: Money;
  service_charge_tax: Money;
  tax_total: Money;
  tax_breakdown: { rate: string; taxable_excl: string; tax: string; total_incl: string }[];
  total_before_rounding: Money;
  rounding_adjustment: Money;
  total: Money;
  total_excl_tax: Money;
  breakdown: Record<string, Money>;
  line_ids?: number[];
};

export type Payment = {
  id: number;
  method: "cash" | "card" | "receivable";
  method_label: string;
  amount: Money;
  tendered: Money | null;
  change_amount: Money;
  paid_at: string;
  status: string;
  card_reference: string;
  note: string;
  business_date: string;
};

export type CheckoutBrief = {
  id: number;
  number: number;
  kind: "full" | "partial";
  status: "confirmed" | "voided";
  business_date: string;
  confirmed_at: string;
  total: Money;
  payments: Payment[];
};

export type Refund = {
  id: number;
  checkout: number;
  payment: number | null;
  business_date: string;
  method: string;
  method_label: string;
  amount: Money;
  reason: string;
  refunded_at: string;
  recorded_by_name: string;
  terminal_refund_done: boolean;
  note: string;
};

export type Checkout = CheckoutBrief & {
  visit: number;
  visit_slip_number: number;
  visit_table_name: string;
  status_label: string;
  confirmed_by_name: string;
  subtotal: Money;
  discount_total: Money;
  service_charge: Money;
  tax_total: Money;
  tax_breakdown: { rate: string; taxable_excl: string; tax: string; total_incl: string }[];
  total_before_rounding: Money;
  rounding_adjustment: Money;
  total_excl_tax: Money;
  breakdown: Record<string, Money>;
  settings_snapshot: Record<string, unknown>;
  void_reason: string;
  voided_at: string | null;
  voided_by_name: string;
  note: string;
  refunds: Refund[];
  lines: OrderLine[];
};

export type Visit = {
  id: number;
  business_date: string;
  slip_number: number;
  table: number;
  table_name: string;
  status: "open" | "closed" | "cancelled";
  status_label: string;
  opened_at: string;
  closed_at: string | null;
  guest_count: number;
  customer: number | null;
  customer_name: string;
  customer_display: string;
  primary_employee: number | null;
  primary_employee_name: string | null;
  note: string;
  cancel_reason: string;
  course_entries: CourseEntry[];
  lines: OrderLine[];
  checkouts: CheckoutBrief[];
  created_at: string;
  quote?: Quote;
  server_now?: string;
};

export type VisitBrief = {
  id: number;
  business_date: string;
  slip_number: number;
  table: number;
  table_name: string;
  status: "open" | "closed" | "cancelled";
  opened_at: string;
  guest_count: number;
  customer_display: string;
  primary_employee_name: string | null;
  note: string;
  course_entries: CourseEntry[];
  line_count: number;
  running_total: Money | null;
};

export type FloorState = {
  server_now: string;
  business_date: string;
  business_day: { id: number; date: string; status: string; opening_float: Money } | null;
  alarm_mode: "10" | "5" | "both";
  tables: { id: number; name: string; capacity: number; visits: VisitBrief[] }[];
  alarms: FloorAlarm[];
  open_visit_count: number;
};

export type Handwrite = {
  checkout_id: number;
  checkout_number: number;
  status: string;
  slip_number: number;
  table_name: string;
  business_date: string;
  confirmed_at: string;
  kind: "full" | "partial";
  scope_note: string;
  total: Money;
  total_excl_tax: Money;
  tax_total: Money;
  tax_breakdown: { rate: string; taxable_excl: string; tax: string; total_incl: string }[];
  service_charge: Money;
  discount_total: Money;
  rounding_adjustment: Money;
  cash_paid: Money;
  card_paid: Money;
  receivable_amount: Money;
  cash_tendered: Money;
  cash_change: Money;
  visit_total_all_checkouts: Money;
  other_checkouts: { id: number; number: number; total: Money }[];
  lines: { name: string; amount: Money; line_type: string }[];
};

export type Employee = {
  id: number;
  full_name: string;
  display_name: string;
  hired_on: string | null;
  left_on: string | null;
  is_active: boolean;
  note: string;
  user: number | null;
  user_username: string | null;
  login_enabled: boolean;
  assignments: { id: number; store: number; store_name: string; role: string; is_active: boolean; started_on: string | null; ended_on: string | null }[];
  this_store_role: string | null;
  /** この店舗での今の出勤状態（一覧取得時のみ） */
  shift_status?: "working" | "on_break" | "left" | "off" | null;
  shift_since?: string | null;
  created_at: string;
};

export type CashMovement = {
  id: number;
  business_day: number;
  kind: string;
  kind_label: string;
  amount: Money;
  reason: string;
  employee: number | null;
  employee_name: string | null;
  occurred_at: string;
  performed_by_name: string;
  is_voided: boolean;
  void_reason: string;
};

export type DaySummary = {
  opening_float: Money;
  cash_sales: Money;
  card_sales: Money;
  receivable_sales: Money;
  sales_total: Money;
  checkout_count: number;
  guest_count: number;
  receivable_collected_cash: Money;
  receivable_collected_card: Money;
  cash_refunds: Money;
  card_refunds: Money;
  cash_in_other: Money;
  cash_out_expense: Money;
  cash_out_daily_pay: Money;
  cash_out_other: Money;
  cash_out_mid_collection: Money;
  theoretical_cash: Money;
  bank_deposits_total: Money;
  net_estimate: Money;
};

export type DailyPayout = {
  employee_id: number;
  employee_name: string;
  work_date: string;
  status: "working" | "ready" | "partial" | "paid" | "nothing" | "missing_wage" | "absent";
  working: boolean;
  on_break: boolean;
  open_shift_id: number | null;
  shifts: { shift_id: number; clock_in: string; clock_out: string | null; minutes: number; hourly_wage: Money | null; amount: Money }[];
  worked_minutes: number;
  wage_amount: Money;
  back_confirmed: Money;
  back_pending: Money;
  gross_amount: Money;
  paid_amount: Money;
  withheld_amount: Money;
  deducted_amount: Money;
  settled_amount: Money;
  /** 未精算の報酬（源泉所得税・その他控除を引く前） */
  remaining_gross: Money;
  /** 今渡す額（源泉所得税・その他控除を引いた後） */
  remaining_amount: Money;
  missing_wage_setting: boolean;
  items: PayItem[];
  tax: PayoutTax | null;
  payments: {
    id: number;
    amount: Money;
    withholding_amount: Money;
    deduction_amount: Money;
    deduction_items: PayoutDeductionRow[];
    paid_at: string;
    cash_movement_id: number | null;
    method: string;
  }[];
};

/** 差し引く控除（optional: 店舗の控除項目でチェックしたもの） */
export type PayoutDeductionRow = { code: string; label: string; calc: string; value: Money; amount: Money; optional?: boolean };

/** 店舗の控除項目の選択肢（amount: チェックしたら今回引く額、done: 同じ日の支払いで引き済み） */
export type PayoutDeductionOption = { code: string; label: string; percent: Money; default_on: boolean; amount: Money; done: boolean; checked: boolean };

export type PayItem = { code: string; label: string; amount: Money; withholding_target: boolean };

/** お渡し時の源泉所得税の見込み（サーバー計算） */
export type PayoutTax = {
  withholding_enabled: boolean;
  category: "hostess_fee" | "salary" | "undetermined";
  category_label: string;
  confirmed: boolean;
  /** disabled: 店舗設定で源泉所得税を差し引かない */
  status: "calculated" | "needs_review" | "external" | "settled" | "disabled" | "none";
  reasons: string[];
  period_start: string | null;
  period_end: string | null;
  target_amount: Money;
  deduction_available: Money;
  deduction_applied: Money;
  withholding_amount: Money;
  rate_percent: Money;
  rule_version: string;
  explanation: string;
  other_deductions: PayoutDeductionRow[];
  other_deduction_amount: Money;
  deduction_options: PayoutDeductionOption[];
  net_amount: Money;
  /** 店舗の控除項目をどれもチェックしないときのお渡し額 */
  net_without_options: Money;
};

export type DailyPayoutList = {
  work_date: string;
  rows: DailyPayout[];
  totals: { gross: Money; paid: Money; withheld: Money; deducted: Money; remaining: Money; unpaid_count: number; working_count: number; tax_review_count: number };
  withholding_enabled: boolean;
  notice: string;
};

export type BusinessDay = {
  id: number;
  date: string;
  status: "open" | "closed";
  opened_at: string;
  opened_by_name: string;
  opening_float: Money;
  opening_float_carried: boolean;
  closed_at: string | null;
  closed_by_name: string;
  cash_sales: Money;
  card_sales: Money;
  receivable_sales: Money;
  theoretical_cash: Money;
  counted_cash: Money | null;
  denominations: Record<string, number>;
  difference: Money | null;
  difference_reason: string;
  next_day_float: Money | null;
  set_aside_amount: Money | null;
  shortage_amount: Money | null;
  note: string;
  revisions: { id: number; changed_by_name: string; changed_at: string; reason: string; before: Record<string, unknown>; after: Record<string, unknown> }[];
  summary: DaySummary;
  precheck: { code: string; message: string; blocking?: boolean }[];
  open_visits?: { visit_id: number; slip_number: number; table_name: string }[];
  movements: CashMovement[];
  bank_deposits: { id: number; amount: Money; deposited_at: string; note: string }[];
};

export type CashCurrent = {
  business_date_today: string;
  day: BusinessDay | null;
  last_closed_day: BusinessDay | null;
  suggested_opening_float: Money;
  suggested_from_previous_day: boolean;
  previous_day: { date: string; next_day_float: Money } | null;
  server_now: string;
};

export type Receivable = {
  id: number;
  checkout: number;
  checkout_number: number;
  customer: number | null;
  customer_name: string | null;
  debtor_name: string;
  amount: Money;
  collected_amount: Money;
  remaining: Money;
  status: string;
  business_date: string;
  note: string;
  collections: { id: number; business_date: string; method: string; amount: Money; collected_at: string; note: string }[];
  created_at: string;
};

export type Customer = {
  id: number;
  store: number;
  store_name: string;
  is_own_store: boolean;
  display_name: string;
  phone: string;
  contact_note: string;
  assigned_employee: number | null;
  assigned_employee_name: string | null;
  memo: string;
  shared_in_company: boolean;
  is_active: boolean;
  active_bottles: number;
  created_at: string;
};

// --- カード決済端末 ---
export type TerminalMode = "off" | "mock" | "mock_disabled" | "sandbox" | "production" | "production_blocked" | "incomplete";

export type TerminalStatusInfo = {
  mode: TerminalMode;
  enabled: boolean;
  is_mock: boolean;
  terminals: { id: number; name: string }[];
  needs_attention: TerminalPayment[];
};

export type TerminalPaymentStatus = "pending" | "approved" | "completed" | "declined" | "canceled" | "needs_attention";

export type TerminalPayment = {
  id: number;
  visit: number;
  slip_number: number;
  table_name: string;
  terminal: number;
  terminal_name: string;
  provider: string;
  is_mock: boolean;
  amount: string;
  cash_amount: string;
  cash_tendered: string | null;
  change_amount: string;
  line_ids: number[];
  status: TerminalPaymentStatus;
  status_label: string;
  error_message: string;
  provider_payment_id: string;
  checkout: number | null;
  checkout_number: number | null;
  requested_by_name: string;
  created_at: string;
  finished_at: string | null;
  resolved_at: string | null;
  resolution_note: string;
};

export type TerminalConfig = {
  provider: "none" | "mock" | "square";
  square_environment: "sandbox" | "production";
  square_location_id: string;
  square_access_token?: string;
  square_webhook_signature_key?: string;
  square_access_token_masked: string;
  square_webhook_signature_key_masked: string;
  square_webhook_url: string;
  note: string;
  webhook_path: string | null;
  mode: TerminalMode;
  live_enabled: boolean;
  mock_allowed: boolean;
  updated_at: string;
};

export type CardTerminalDevice = {
  id: number;
  name: string;
  provider: string;
  provider_label: string;
  device_id: string;
  status: "unpaired" | "pairing" | "paired" | "disabled";
  status_label: string;
  pairing_code: string;
  pairing_expires_at: string | null;
  paired_at: string | null;
  is_active: boolean;
};

// --- 経理（源泉所得税・freee） -------------------------------------------------
export type TaxPayer = {
  id: number;
  name: string;
  entity_type: "corporation" | "sole_proprietor";
  entity_type_label: string;
  corporate_number: string;
  representative_name: string;
  address: string;
  phone: string;
  tax_office_name: string;
  reference_number: string;
  payroll_due_special: boolean;
  fiscal_year_end_month: number;
  filing_method: "" | "freee" | "nta_corner";
  is_active: boolean;
  note: string;
  store_ids: number[];
};

export type AccountingAlert = {
  level: "overdue" | "soon" | "info";
  category: string;
  category_label: string;
  label: string;
  due_date: string;
  status: string;
  status_label: string;
  unpaid_total: Money;
  period_start: string;
};

export type AccountingOverview = {
  taxpayer: TaxPayer | null;
  taxpayers: TaxPayer[];
  stores: { id: number; name: string; taxpayer_id: number | null; withholding_enabled: boolean }[];
  alerts: AccountingAlert[];
  etax_links: { label: string; url: string }[];
  role: string;
  freee_live_enabled: boolean;
};

export type WithholdingTotals = {
  gross_total: Money;
  target_total?: Money;
  deduction_total?: Money;
  withheld_total: Money;
  adjustment_total: Money;
  tax_total: Money;
  people: number;
  payment_count: number;
  needs_review_count: number;
  needs_review_amount?: Money;
  noxia_salary_paid?: Money;
  noxia_salary_count?: number;
};

export type WithholdingRecordBrief = {
  id: number;
  store_name: string;
  employee_id: number;
  employee_name: string;
  paid_on: string;
  work_date: string | null;
  tax_category: string;
  status: string;
  status_label: string;
  review_reasons: string[];
  period_start: string | null;
  period_end: string | null;
  period_days: number;
  gross_amount: Money;
  target_amount: Money;
  deduction_applied: Money;
  withholding_amount: Money;
  other_deduction_amount: Money;
  net_amount: Money;
  rule_version: string;
  is_voided: boolean;
  calc_basis: { explanation?: string; reasons?: string[] };
};

export type WithholdingForm = {
  form: string;
  notice: string;
  taxpayer: { name: string; entity_type: string; address: string; phone: string; tax_office_name: string; reference_number: string };
  nendo: string;
  nendo_note: string;
  nouki: { label: string; code: string };
  rows: { code?: string; kind?: string; label: string; people: number; payment_amount: Money; tax: Money; people_note?: string }[];
  tekiyo: { label: string; amount: Money }[];
  penalty_note: string;
  total: Money;
  due_date: string;
  zero_tax_note?: string;
  warnings: string[];
};

export type WithholdingMonthView = {
  taxpayer_id: number;
  taxpayer_name: string;
  category: "hostess_fee" | "salary";
  category_label: string;
  period_start: string;
  period_end: string;
  label: string;
  due_date: string;
  days_to_due: number;
  special_due: boolean;
  status: string;
  status_label: string;
  month_id: number | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  advisor_note: string;
  advisor_checked_at: string | null;
  totals: WithholdingTotals;
  remitted_total: Money;
  unpaid_total: Money;
  snapshot_diff: Record<string, { before: string | undefined; after: string }>;
  by_store?: { store_id: number; store_name: string; people: number; count: number; gross: Money; target: Money; deduction: Money; withholding: Money }[];
  by_employee?: { employee_id: number; employee_name: string; count: number; gross: Money; target: Money; deduction: Money; withholding: Money; stores: string[] }[];
  needs_review?: WithholdingRecordBrief[];
  rows?: { kind: string; label: string; people: number; gross: Money; withholding: Money; count: number }[];
  entries?: { id: number; kind_label: string; paid_on: string; employee_name: string | null; gross_amount: Money; withholding_amount: Money; note: string; is_voided: boolean }[];
  adjustments?: { id: number; kind_label: string; applies_on: string; amount_delta: Money; reason: string; is_voided: boolean }[];
  remittances?: { id: number; paid_on: string; amount: Money; method_label: string; note: string; is_voided: boolean }[];
  form?: WithholdingForm;
  etax_links?: { label: string; url: string }[];
};

export type CastContract = {
  id: number;
  employee: number;
  employee_name: string;
  contract_type: "employment" | "outsourcing" | "other";
  contract_type_label: string;
  start_date: string;
  end_date: string | null;
  calc_period: "daily" | "weekly" | "semimonthly" | "monthly" | "unset";
  calc_period_label: string;
  payment_method: string;
  payment_note: string;
  tax_category: "salary" | "hostess_fee" | "undetermined";
  tax_category_label: string;
  tax_review_status: "unreviewed" | "confirmed" | "needs_review";
  tax_review_status_label: string;
  tax_reviewed_by_name: string | null;
  tax_reviewed_at: string | null;
  review_checklist: Record<string, boolean>;
  review_note: string;
  advisor_note: string;
  deductions: { code: string; label: string; calc: "percent" | "per_day" | "per_payment"; value: string }[];
  document_name: string;
  has_document: boolean;
  is_active: boolean;
};

export type Journal = {
  id: number;
  sync_key: string;
  source_type: string;
  store_name: string | null;
  issue_date: string;
  description: string;
  lines: { side: "debit" | "credit"; key: string; amount: Money; tax: string; memo: string }[];
  warnings: string[];
  revision: number;
  is_void: boolean;
  sync_id: number;
  sync_status: string;
  sync_status_label: string;
  freee_id: number | null;
  last_error: string;
};

export type FreeeStatus = {
  mode: "mock" | "live";
  status: "disconnected" | "connected" | "error";
  freee_company_id: number | null;
  freee_company_name: string;
  connected_at: string | null;
  sync_from: string | null;
  has_token: boolean;
  last_error: string;
  live_enabled: boolean;
  sync_counts: Record<string, number>;
  result?: { sent: number; failed: number };
};
