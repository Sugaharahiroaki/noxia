"use client";

import Link from "next/link";
import { useReportLinks } from "@/components/ReportKit";
import { Empty, PageHeader } from "@/components/ui";

/** 集計の入口（管理用。見るだけの画面の一覧） */
export default function ReportsIndexPage() {
  const links = useReportLinks();
  return (
    <div className="max-w-5xl">
      <PageHeader title="集計" subtitle="店舗タブレットで登録した伝票・勤怠・入出金を、管理用に集計して見る画面です。ここからは金額を変更しません（訂正は「売上・会計履歴」の取消・返金で行います）。" />
      {links.length === 0 && <Empty>見られる集計がありません（権限をオーナーに確認してください）。</Empty>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {links.map((l) => (
          <Link key={l.href} href={l.href} className="card block">
            <div className="text-xl font-bold">{l.label}</div>
            {l.note && (
              <div className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
                {l.note}
              </div>
            )}
          </Link>
        ))}
      </div>
    </div>
  );
}
