/** キャスト・スタッフ集計の列の定義（月次の集計表と、個人の日別表で同じものを使う）。用語は「報酬」で統一する。 */
import type { Money } from "./types";
import type { ReportSettings } from "./types_reports";

export type PayRow = {
  wage: Money;
  back_nomination: Money;
  back_jounai: Money;
  back_douhan: Money;
  back_yobi_subtotal: Money;
  back_douhan_subtotal: Money;
  back_drink: Money;
  back_bottle: Money;
  back_food: Money;
  back_other: Money;
  back_total: Money;
  bonus: Money;
  minus: Money;
  gross: Money;
  paid: Money;
  withheld: Money;
  deducted: Money;
  balance: Money;
  sales_drink: Money;
  sales_bottle: Money;
  sales_food: Money;
  order_subtotal: Money;
  yobi_subtotal: Money;
  yobi_total: Money;
  douhan_subtotal: Money;
  douhan_total: Money;
  minutes: number;
  days: string;
  qty_drink: string;
  qty_bottle: string;
  qty_food: string;
  nomination_count: string;
  jounai_count: string;
  douhan_count: string;
  nomination_groups: string;
  jounai_groups: string;
  douhan_groups: string;
  avg_hourly: Money | null;
  pay_ratio: string | null;
};

export type PersonRow = PayRow & { employee_id: number; employee_name: string; is_active: boolean; role: string };
export type PeopleReport = { month: string; from: string; to: string; rows: PersonRow[]; totals: PayRow; settings: ReportSettings };
export type PayShift = { clock_in: string; clock_out: string | null; minutes: number; hourly_wage: Money | null; amount: Money };
export type PersonReport = {
  month: string;
  from: string;
  to: string;
  employee: { id: number; name: string; full_name: string; role: string };
  store_name: string;
  totals: PayRow;
  days: (PayRow & { date: string; shifts: PayShift[] })[];
  settings: ReportSettings;
};

export type PayColKind = "money" | "count" | "ratio" | "hours";
export type PayCol = { key: keyof PayRow; label: string; kind: PayColKind; strong?: boolean; withholdingOnly?: boolean };
export type PayGroup = { key: string; label: string; cols: PayCol[]; castOnly?: boolean };

export const PAY_GROUPS: PayGroup[] = [
  {
    key: "summary",
    label: "報酬",
    cols: [
      { key: "gross", label: "総支給額", kind: "money", strong: true },
      { key: "balance", label: "残り支給額", kind: "money", strong: true },
    ],
  },
  {
    key: "sales",
    label: "売上",
    castOnly: true,
    cols: [
      { key: "order_subtotal", label: "オーダー小計", kind: "money" },
      { key: "yobi_total", label: "呼び本計", kind: "money" },
      { key: "yobi_subtotal", label: "呼び小計", kind: "money" },
      { key: "douhan_total", label: "同伴本計", kind: "money" },
      { key: "douhan_subtotal", label: "同伴小計", kind: "money" },
      { key: "pay_ratio", label: "給率", kind: "ratio" },
    ],
  },
  {
    key: "time",
    label: "時間報酬",
    cols: [
      { key: "days", label: "勤務日数", kind: "count" },
      { key: "avg_hourly", label: "平均時給", kind: "money" },
      { key: "minutes", label: "時間", kind: "hours" },
      { key: "wage", label: "報酬額", kind: "money" },
    ],
  },
  {
    key: "yobi",
    label: "指名・同伴のバック",
    castOnly: true,
    cols: [
      { key: "nomination_groups", label: "本指名 組数", kind: "count" },
      { key: "nomination_count", label: "回数", kind: "count" },
      { key: "back_nomination", label: "金額", kind: "money" },
      { key: "jounai_groups", label: "場内 組数", kind: "count" },
      { key: "jounai_count", label: "回数", kind: "count" },
      { key: "back_jounai", label: "金額", kind: "money" },
      { key: "douhan_groups", label: "同伴 組数", kind: "count" },
      { key: "douhan_count", label: "回数", kind: "count" },
      { key: "back_douhan", label: "金額", kind: "money" },
      { key: "back_yobi_subtotal", label: "呼び小計バック", kind: "money" },
      { key: "back_douhan_subtotal", label: "同伴小計バック", kind: "money" },
    ],
  },
  {
    key: "items",
    label: "商品のバック",
    castOnly: true,
    cols: [
      { key: "sales_drink", label: "ドリンク 売上", kind: "money" },
      { key: "qty_drink", label: "本数", kind: "count" },
      { key: "back_drink", label: "金額", kind: "money" },
      { key: "sales_bottle", label: "ボトル 売上", kind: "money" },
      { key: "qty_bottle", label: "本数", kind: "count" },
      { key: "back_bottle", label: "金額", kind: "money" },
      { key: "sales_food", label: "フード 売上", kind: "money" },
      { key: "qty_food", label: "回数", kind: "count" },
      { key: "back_food", label: "金額", kind: "money" },
      { key: "back_other", label: "その他", kind: "money" },
    ],
  },
  {
    key: "total",
    label: "バック計・調整",
    cols: [
      { key: "back_total", label: "バック計", kind: "money" },
      { key: "bonus", label: "ボーナス", kind: "money" },
      { key: "minus", label: "マイナス", kind: "money" },
    ],
  },
  {
    key: "deduct",
    label: "控除・支払い",
    cols: [
      { key: "deducted", label: "控除（厚生費など）", kind: "money" },
      { key: "withheld", label: "源泉所得税", kind: "money", withholdingOnly: true },
      { key: "paid", label: "日払い（渡した額）", kind: "money" },
    ],
  },
];

/** 役割と店舗の設定に合わせて、出す列だけにする（源泉所得税の列は、源泉徴収をしている店舗だけ） */
export function payGroups(opts: { cast: boolean; withholding: boolean; hidden?: Set<string> }): PayGroup[] {
  return PAY_GROUPS.filter((g) => (opts.cast || !g.castOnly) && !opts.hidden?.has(g.key))
    .map((g) => ({ ...g, cols: g.cols.filter((c) => opts.withholding || !c.withholdingOnly) }))
    .filter((g) => g.cols.length > 0);
}

export function payCell(row: PayRow, col: PayCol): string {
  const v = row[col.key];
  if (v === null || v === undefined || v === "") return "--";
  const n = Number(v);
  if (Number.isNaN(n) || n === 0) return "--";
  if (col.kind === "ratio") return `${n.toFixed(1)}%`;
  if (col.kind === "hours") return `${Math.floor(n / 60)}:${String(Math.round(n % 60)).padStart(2, "0")}`;
  if (col.kind === "count") return n.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  return (n < 0 ? "−" : "") + Math.round(Math.abs(n)).toLocaleString("ja-JP");
}
