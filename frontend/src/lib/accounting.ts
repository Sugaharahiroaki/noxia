/** 経理画面の共通の表示ラベル（値の判定・計算はサーバー側） */

export const MONTH_STATUS_BADGE: Record<string, string> = {
  not_aggregated: "badge",
  aggregated: "badge badge-accent",
  needs_review: "badge badge-warn",
  owner_confirmed: "badge badge-accent",
  awaiting_payment: "badge badge-orange",
  paid: "badge badge-ok",
  overdue: "badge badge-danger",
  corrected: "badge badge-danger",
};

export const CATEGORY_TABS = [
  { key: "hostess_fee", label: "ホステス報酬（毎月納付）" },
  { key: "salary", label: "給与（給与計算ソフトの結果）" },
] as const;

export const REMITTANCE_METHODS = [
  { key: "etax_direct", label: "e-Tax（ダイレクト納付）" },
  { key: "etax_banking", label: "e-Tax（インターネットバンキング等）" },
  { key: "credit_card", label: "クレジットカード納付" },
  { key: "qr_convenience", label: "コンビニ納付（QRコード）" },
  { key: "counter", label: "金融機関・税務署の窓口（納付書）" },
  { key: "other", label: "その他" },
];

export const PAID_FROM = [
  { key: "bank", label: "事業用口座" },
  { key: "cash_register", label: "この店舗のレジ現金" },
  { key: "owner_personal", label: "経営者の個人のお金" },
];

export const ADJUSTMENT_KINDS = [
  { key: "refund", label: "過納額の精算（過払い報酬の返還など）" },
  { key: "additional", label: "追加徴収（計算誤りの訂正など）" },
  { key: "unwithheld", label: "支払時に未徴収だった税額（支払者が納付）" },
  { key: "correction", label: "その他の訂正" },
];

export const CONTRACT_TYPES = [
  { key: "employment", label: "雇用契約" },
  { key: "outsourcing", label: "業務委託契約" },
  { key: "other", label: "その他（税務確認が必要）" },
];

export const CALC_PERIODS = [
  { key: "daily", label: "日ごと" },
  { key: "weekly", label: "週ごと（月曜〜日曜）" },
  { key: "semimonthly", label: "半月ごと（1〜15日／16日〜末日）" },
  { key: "monthly", label: "月ごと（1日〜末日）" },
  { key: "unset", label: "未設定（要確認）" },
];

export const EXPENSE_ACCOUNTS = [
  { key: "purchases", label: "仕入高（酒類・食材など）" },
  { key: "rent", label: "地代家賃" },
  { key: "utilities", label: "水道光熱費" },
  { key: "supplies", label: "消耗品費" },
  { key: "advertising", label: "広告宣伝費" },
  { key: "communication", label: "通信費" },
  { key: "entertainment", label: "接待交際費" },
  { key: "travel", label: "旅費交通費" },
  { key: "repair", label: "修繕費" },
  { key: "fees", label: "支払手数料" },
  { key: "welfare", label: "福利厚生費" },
  { key: "misc_expense", label: "雑費" },
];

export function thisMonth(): string {
  const d = new Date();
  const jst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Tokyo" }));
  return `${jst.getFullYear()}-${String(jst.getMonth() + 1).padStart(2, "0")}`;
}

export function prevMonth(): string {
  const [y, m] = thisMonth().split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

export function monthRange(month: string): { from: string; to: string } {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, "0")}` };
}

export function todayJst(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}
