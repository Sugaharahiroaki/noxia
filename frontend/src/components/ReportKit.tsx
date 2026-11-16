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

/** 件数・人数など（0 は「--」）。小数は 1 桁まで */
export function cnt(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n) || n === 0) return "--";
  return n.toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

export type ReportLink = { href: string; label: string; caps?: string[]; note?: string };

/** 集計画面の一覧（フェーズごとに足す） */
export const REPORT_LINKS: ReportLink[] = [{ href: "/reports/slips", label: "伝票一覧", caps: ["sales.view"], note: "期間・支払方法・値引き・取消で絞り込み。伝票ごとのキャスト別の売上小計" }];

export function useReportLinks(): ReportLink[] {
  const { can } = useSession();
  return REPORT_LINKS.filter((l) => !l.caps || can(...l.caps));
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
