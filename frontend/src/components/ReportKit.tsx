"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/lib/session";

/**
 * 管理用の集計画面（/reports/...）の共通部品。見た目は NOXIA の管理画面のまま。
 * 表の決まり: 金額は右寄せ・3 桁区切り、合計行は表の先頭、0 は「--」。
 */

/** 金額（3 桁区切り。0・空は「--」） */
export function amt(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n) || n === 0) return "--";
  return (n < 0 ? "−" : "") + Math.round(Math.abs(n)).toLocaleString("ja-JP");
}

/** 伝票 ID・会計 ID の表示（4 文字ごとに区切る。例: K7QM-2XRA-9BHT） */
export function fmtId(value: string | null | undefined): string {
  if (!value) return "--";
  return value.match(/.{1,4}/g)?.join("-") ?? value;
}

/** 件数・人数など（0 は「--」）。小数は 1 桁まで */
export function cnt(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n) || n === 0) return "--";
  return n.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

export type ReportLink = { href: string; label: string; caps?: string[]; note?: string };

/** 集計画面の一覧（フェーズごとに足す） */
export const REPORT_LINKS: ReportLink[] = [
  { href: "/reports/monthly", label: "収支（月次）", caps: ["dashboard.view"], note: "1 行 = 1 営業日。売上・女子売上・人件費・入出金・粗利。日付を押すと営業日報へ" },
  { href: "/reports/daily", label: "営業日報（日次）", caps: ["dashboard.view"], note: "当日・累計・1 営業日あたりの平均、現金管理、入出金、その日の伝票" },
  { href: "/reports/slips", label: "伝票一覧", caps: ["sales.view"], note: "期間・支払方法・値引き・取消で絞り込み。伝票ごとのキャスト別の売上小計" },
  { href: "/reports/cash", label: "レジ精算（月間）", caps: ["dashboard.view"], note: "営業日ごとの釣銭・現金売上・入出金・理論現金・実際の現金・過不足・預金" },
  { href: "/reports/cast", label: "キャスト集計", caps: ["payroll.view_all"], note: "キャストごとの売上（呼び・同伴）と報酬（時間報酬・バック・日払い・残り）。名前から報酬支払明細へ" },
  { href: "/reports/staff", label: "スタッフ集計", caps: ["payroll.view_all"], note: "スタッフごとの報酬（時間報酬・調整・日払い・残り）" },
];

/** 割合（%）。null は「--」 */
export function pct(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  return `${Number(value).toFixed(2)}%`;
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];
/** 2026-11-17 → 17（火） */
export function dayLabel(dateStr: string): { day: number; weekday: string; weekend: boolean } {
  const d = new Date(`${dateStr}T00:00:00+09:00`);
  const w = new Date(d.getTime() + 9 * 3600 * 1000).getUTCDay();
  return { day: Number(dateStr.slice(8, 10)), weekday: WEEKDAYS[w], weekend: w === 0 || w === 6 };
}

export function useReportLinks(): ReportLink[] {
  const { can, me } = useSession();
  const links = REPORT_LINKS.filter((l) => !l.caps || can(...l.caps));
  // 全員分は見られないが自分の分は見られる人（キャスト本人など）には、自分の報酬支払明細だけを出す
  if (!can("payroll.view_all") && can("payroll.view_self") && me?.employee) {
    links.push({ href: `/reports/cast/${me.employee.id}`, label: "自分の報酬支払明細", note: "自分の勤務・バック・日払いの月ごとの明細と日別表" });
  }
  return links;
}

/** 集計画面の上に出す切替（いまの画面を強調） */
export function ReportNav() {
  const pathname = usePathname();
  const links = useReportLinks();
  return (
    <nav className="flex flex-wrap gap-2 mb-3" aria-label="集計の画面">
      {links.map((l) => (
        <Link key={l.href} href={l.href} className={`btn btn-sm ${pathname.startsWith(l.href) ? "btn-primary" : ""}`} aria-current={pathname.startsWith(l.href) ? "page" : undefined}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}

/** 横に長い表の入れ物（横スクロール。先頭の列を固定するときは th/td に .sticky-col を付ける） */
export function ReportTable({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div className="scroll-x card" style={{ padding: 0 }} role="region" aria-label={label} tabIndex={0}>
      <table className="table report-table">{children}</table>
    </div>
  );
}

/** 表の下の注記 */
export function ReportNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
      {children}
    </p>
  );
}
