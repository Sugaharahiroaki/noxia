"use client";

import { useState } from "react";
import { RequireCaps } from "@/components/common";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { amt, cnt, ExportButton, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { downloadFile, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Tx, useTx } from "@/lib/terms";
import { useSession } from "@/lib/session";
import type { Money } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type GroupBy = "tag" | "day" | "month";
type TagRow = { period: string; tag_id: number; tag_name: string; color: string; count: number; guests: number; subtotal: Money; cash: Money; card: Money; receivable: Money; total: Money };
type TagReport = { from: string; to: string; group_by: GroupBy; rows: TagRow[]; totals: Omit<TagRow, "period" | "tag_id" | "tag_name" | "color">; tagged_slips: number; note: string };
const MODES: { key: GroupBy; label: string }[] = [
  { key: "tag", label: "タグ別" },
  { key: "day", label: "日別" },
  { key: "month", label: "月別" },
];

export default function TagReportPage() {
  return (
    <RequireCaps caps={["sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const { can } = useSession();
  const toast = useToast();
  const tx = useTx();
  const [range, setRange, businessDate] = useDateRange();
  const [groupBy, setGroupBy] = useState<GroupBy>("tag");
  const qs = range ? `from=${range.from}&to=${range.to}&group_by=${groupBy}` : null;
  const q = useLoad<TagReport>(qs ? `/api/reports/tags?${qs}` : null);
  const d = q.data;
  const period = (p: string) => (groupBy === "day" ? fmtDate(p) : groupBy === "month" ? `${p.slice(0, 4)}年${Number(p.slice(5, 7))}月` : "");

  return (
    <div>
      <PageHeader
        title={tx("タグ集計")}
        subtitle="伝票に付けたタグごとの件数・客数・金額。金額は確定した会計の合計です。"
        actions={
          can("export.sales") && qs ? (
            <>
            <ExportButton path={`/api/reports/export/tags.xlsx?${qs}`} />
            <button className="btn btn-sm" onClick={() => downloadFile(`/api/reports/export/tags.csv?${qs}`).catch((e) => toast.show({ message: errorMessage(e), tone: "error" }))}>
              CSV 出力
            </button>
            </>
          ) : null
        }
      />
      <ReportNav />
      <div className="space-y-3">
        <DateRangeBar range={range} onChange={setRange} businessDate={businessDate} />
        <div className="flex flex-wrap gap-2" role="group" aria-label="集計の単位">
          {MODES.map((m) => (
            <button key={m.key} className={`btn btn-sm ${groupBy === m.key ? "btn-primary" : ""}`} onClick={() => setGroupBy(m.key)} aria-pressed={groupBy === m.key}>
              {m.label}
            </button>
          ))}
        </div>
        <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
        {q.loading && <Spinner />}
        {d && (
          <>
            <ReportTable label="タグ集計">
              <thead>
                <tr>
                  {groupBy !== "tag" && <th>{groupBy === "day" ? "営業日" : "月"}</th>}
                  <th>
                    <Tx>タグ</Tx>
                  </th>
                  <th className="r">件数</th>
                  <th className="r">客数</th>
                  <th className="r">伝票小計</th>
                  <th className="r">現金</th>
                  <th className="r">カード</th>
                  <th className="r">売掛</th>
                  <th className="r">販売額</th>
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  {groupBy !== "tag" && <td />}
                  <td>
                    <Tx>タグの行の合計（重複あり）</Tx>
                  </td>
                  <td className="r">{cnt(d.totals.count)}</td>
                  <td className="r">{cnt(d.totals.guests)}</td>
                  <td className="r">{amt(d.totals.subtotal)}</td>
                  <td className="r">{amt(d.totals.cash)}</td>
                  <td className="r">{amt(d.totals.card)}</td>
                  <td className="r">{amt(d.totals.receivable)}</td>
                  <td className="r">{amt(d.totals.total)}</td>
                </tr>
                {d.rows.map((r) => (
                  <tr key={`${r.period}-${r.tag_id}`}>
                    {groupBy !== "tag" && <td className="num">{period(r.period)}</td>}
                    <td>
                      <span className="badge" style={r.color ? { background: r.color, color: "#fff", borderColor: r.color } : undefined}>
                        {r.tag_name}
                      </span>
                    </td>
                    <td className="r">{cnt(r.count)}</td>
                    <td className="r">{cnt(r.guests)}</td>
                    <td className="r">{amt(r.subtotal)}</td>
                    <td className="r">{amt(r.cash)}</td>
                    <td className="r">{amt(r.card)}</td>
                    <td className="r">{amt(r.receivable)}</td>
                    <td className="r font-semibold">{amt(r.total)}</td>
                  </tr>
                ))}
              </tbody>
            </ReportTable>
            {d.rows.length === 0 && <Empty>この期間にタグの付いた伝票はありません。</Empty>}
            <ReportNote>
              {d.note} この期間でタグの付いた伝票は {d.tagged_slips} 件です。タグの追加・色・ホールへの表示は POS 設定の「タグ」で行います。
            </ReportNote>
          </>
        )}
      </div>
    </div>
  );
}
