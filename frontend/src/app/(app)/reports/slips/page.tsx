"use client";

import Link from "next/link";
import { useState } from "react";
import { RequireCaps } from "@/components/common";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { amt, cnt, ExportButton, fmtId, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtTime } from "@/lib/format";
import { Tx } from "@/lib/terms";
import { useSession } from "@/lib/session";
import type { StoreSettings } from "@/lib/types";
import type { SlipList } from "@/lib/types_reports";
import { useLoad } from "@/lib/useLoad";

type Filter = "" | "cash" | "card" | "receivable" | "discount" | "cancelled";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "", label: "全て" },
  { key: "cash", label: "現金" },
  { key: "card", label: "カード" },
  { key: "receivable", label: "売掛（ツケ）" },
  { key: "discount", label: "値引きあり" },
  { key: "cancelled", label: "取消済み" },
];

export default function SlipListPage() {
  return (
    <RequireCaps caps={["sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const [range, setRange, businessDate] = useDateRange();
  const [filter, setFilter] = useState<Filter>("");
  const qs = range
    ? `from=${range.from}&to=${range.to}` + (filter === "discount" ? "&discount=1" : filter === "cancelled" ? "&cancelled=1" : filter ? `&payment=${filter}` : "")
    : null;
  const q = useLoad<SlipList>(qs ? `/api/reports/slips?${qs}` : null);
  const d = q.data;
  const t = d?.totals;

  return (
    <div>
      <PageHeader title="伝票一覧" subtitle="営業日ごとの伝票。金額は確定した会計（取消を除く）の合計です。営業中の伝票は会計前なので金額は「--」になります。"
        actions={<ExportButton path={range ? `/api/reports/export/slips.xlsx?from=${range.from}&to=${range.to}` : null} cap="export.sales" label="XLSX 出力（期間内のすべて）" />}
      />
      <ReportNav />
      <div className="space-y-3">
        <DateRangeBar range={range} onChange={setRange} businessDate={businessDate} />
        <div className="flex flex-wrap gap-2" role="group" aria-label="抽出">
          {FILTERS.map((f) => (
            <button key={f.key || "all"} className={`btn btn-sm ${filter === f.key ? "btn-primary" : ""}`} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key}>
              {f.label}
            </button>
          ))}
        </div>
        <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
        {q.loading && <Spinner />}
        {d && t && (
          <>
            <ReportTable label="伝票一覧">
              <thead>
                <tr>
                  <th>伝票ID</th>
                  <th>営業日</th>
                  <th>卓</th>
                  <th>入店</th>
                  <th>退店</th>
                  <th className="r">時間</th>
                  <th className="r">客数</th>
                  <th>
                    <Tx>タグ</Tx>
                  </th>
                  <th>
                    <Tx>本指名</Tx>
                  </th>
                  <th>
                    <Tx>場内</Tx>
                  </th>
                  <th>
                    <Tx>同伴</Tx>
                  </th>
                  <th className="r">小計</th>
                  <th className="r">サービス料</th>
                  <th className="r">値引き</th>
                  <th className="r">現金</th>
                  <th className="r">カード</th>
                  <th className="r">売掛</th>
                  <th className="r">合計</th>
                  <th>状態</th>
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  <td>合計 {t.count}件</td>
                  <td colSpan={5} />
                  <td className="r">{cnt(t.guest_count)}</td>
                  <td colSpan={4} />
                  <td className="r">{amt(t.subtotal)}</td>
                  <td className="r">{amt(t.service_charge)}</td>
                  <td className="r">{amt(t.discount)}</td>
                  <td className="r">{amt(t.cash)}</td>
                  <td className="r">{amt(t.card)}</td>
                  <td className="r">{amt(t.receivable)}</td>
                  <td className="r">{amt(t.total)}</td>
                  <td />
                </tr>
                {d.rows.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link href={`/reports/slips/${r.id}`} className="underline font-semibold num" style={{ fontFamily: "ui-monospace, Consolas, monospace" }}>
                        {fmtId(r.public_id)}
                      </Link>
                    </td>
                    <td>{fmtDate(r.business_date)}</td>
                    <td>{r.table_name}</td>
                    <td className="num">{fmtTime(r.opened_at)}</td>
                    <td className="num">{r.closed_at ? fmtTime(r.closed_at) : "--"}</td>
                    <td className="r">{r.minutes === null ? "--" : `${r.minutes}分`}</td>
                    <td className="r">{cnt(r.guest_count)}</td>
                    <td>{r.tags.map((x) => x.name).join("・") || "--"}</td>
                    <td>{r.nomination.join("・") || "--"}</td>
                    <td>{r.jounai.join("・") || "--"}</td>
                    <td>{r.douhan.join("・") || "--"}</td>
                    <td className="r">{amt(r.subtotal)}</td>
                    <td className="r">{amt(r.service_charge)}</td>
                    <td className="r">{amt(r.discount)}</td>
                    <td className="r">{amt(r.cash)}</td>
                    <td className="r">{amt(r.card)}</td>
                    <td className="r">{amt(r.receivable)}</td>
                    <td className="r font-semibold">{amt(r.total)}</td>
                    <td>
                      <span className={`badge ${r.status === "open" ? "badge-accent" : r.status === "cancelled" ? "badge-danger" : ""}`}>{r.status_label}</span>
                      {r.voided_checkouts > 0 && <span className="badge badge-warn ml-1">会計取消 {r.voided_checkouts}</span>}
                      {Number(r.refunds) > 0 && <span className="badge badge-warn ml-1">返金 {amt(r.refunds)}</span>}
                      {r.status === "cancelled" && r.cancel_reason && <span className="text-sm ml-1">{r.cancel_reason}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </ReportTable>
            {d.rows.length === 0 && <Empty>この条件の伝票はありません。</Empty>}
            <ReportNote>
              小計 = 値引き・サービス料の前の明細合計、合計 = 請求額。現金＋カード＋売掛 = 合計。伝票の修正はこの画面ではできません。訂正は{" "}
              <Link href="/sales" className="underline">
                売上・会計履歴
              </Link>{" "}
              の取消・返金で行います。
            </ReportNote>
            <ReportSettingsCard settings={d.settings} onSaved={() => q.reload()} />
          </>
        )}
      </div>
    </div>
  );
}

/** 集計の設定（キャスト売上の分け方・指名回数の数え方）。集計画面の表示だけに効く。変更は店舗設定の編集権限と再認証が必要 */
function ReportSettingsCard({ settings, onSaved }: { settings: SlipList["settings"]; onSaved: () => void }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const canEdit = can("settings.edit");
  async function save(body: Partial<Pick<StoreSettings, never>> & Record<string, string>) {
    setBusy(true);
    try {
      await withReauth(() => api("/api/store/settings", { method: "PUT", body }));
      toast.show({ message: "集計の設定を保存しました" });
      onSaved();
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  const opt = (label: string, on: boolean, body: Record<string, string>) => (
    <button key={label} className={`btn btn-sm ${on ? "btn-primary" : ""}`} disabled={!canEdit || busy || on} onClick={() => save(body)} aria-pressed={on}>
      {label}
    </button>
  );
  return (
    <section className="card space-y-2" aria-label="集計の設定">
      <h2 className="font-bold">集計の設定（キャスト別の売上小計に使う。会計・バック・報酬の計算には使いません）</h2>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm" style={{ minWidth: 260 }}>
          1 つの伝票に指名（同伴）のキャストが複数いるとき
        </span>
        {opt("均等割り（仮）", settings.cast_sales_split_mode === "equal", { cast_sales_split_mode: "equal" })}
        {opt("全員に全額", settings.cast_sales_split_mode === "full_each", { cast_sales_split_mode: "full_each" })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm" style={{ minWidth: 260 }}>
          指名回数の数え方
        </span>
        {opt("1 伝票 1 回", settings.nomination_count_mode === "per_slip", { nomination_count_mode: "per_slip" })}
        {opt("セット数ぶん", settings.nomination_count_mode === "per_set", { nomination_count_mode: "per_set" })}
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        「均等割り」は仮の扱いです。分け方はオーナーの確認待ちです。{!canEdit && "変更には店舗設定の編集権限が必要です。"}
      </p>
    </section>
  );
}
