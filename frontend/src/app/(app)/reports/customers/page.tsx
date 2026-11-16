"use client";

import { useMemo, useState } from "react";
import { RequireCaps } from "@/components/common";
import { amt, cnt, ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Money } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Rank = { label: string; min_visits: number };
type Row = {
  id: number; name: string; store_name: string; own_store: boolean; assigned_employee_name: string; keep_count: number; rank: string;
  visits: number; visits_recent: number; guests: number; first_visit: string | null; last_visit: string | null; avg_minutes: number | null;
  total: Money; unit_price: Money | null; cash: Money; card: Money; receivable: Money; nomination: number; jounai: number; douhan: number;
};
type Report = {
  as_of: string; rank_since: string; rank_months: number; rank_thresholds: Rank[]; sharing_enabled: boolean; rows: Row[];
  totals: { customers: number; keep_count: number; visits: number; visits_recent: number; guests: number; nomination: number; jounai: number; douhan: number;
    total: Money; cash: Money; card: Money; receivable: Money; unit_price: Money | null };
  note: string;
};
type SortKey = "last_visit" | "visits" | "visits_recent" | "total" | "unit_price";
const SORTS: { key: SortKey; label: string }[] = [
  { key: "last_visit", label: "最終来店日" },
  { key: "visits_recent", label: "直近の来店" },
  { key: "visits", label: "来店回数" },
  { key: "total", label: "会計" },
  { key: "unit_price", label: "単価" },
];

/** 滞在時間（分 → 1時間05分） */
function stay(min: number | null): string {
  if (min === null || min === undefined) return "--";
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}時間${String(m).padStart(2, "0")}分` : `${m}分`;
}

export default function CustomerReportPage() {
  return (
    <RequireCaps caps={["customers.view", "sales.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const { can } = useSession();
  const toast = useToast();
  const q = useLoad<Report>("/api/reports/customers");
  const d = q.data;
  const [sort, setSort] = useState<SortKey>("last_visit");
  const [rank, setRank] = useState<string>("");
  const [text, setText] = useState("");
  const rows = useMemo(() => {
    if (!d) return [];
    let list = d.rows.filter((r) => (!rank || (rank === "-" ? !r.rank : r.rank === rank)) && (!text || r.name.includes(text.trim())));
    if (sort !== "last_visit") {
      list = [...list].sort((a, b) => Number(b[sort] ?? 0) - Number(a[sort] ?? 0));
    }
    return list;
  }, [d, sort, rank, text]);

  return (
    <div>
      <PageHeader
        title="お客様分析"
        subtitle="お客様ごとの来店回数・最終来店日・単価・支払方法・指名・キープ・ランク。この店舗への来店だけを数えます。"
        actions={
          can("export.sales") ? (
            <button className="btn btn-sm" onClick={() => downloadFile("/api/reports/export/customers.csv").catch((e) => toast.show({ message: errorMessage(e), tone: "error" }))}>
              CSV 出力
            </button>
          ) : null
        }
      />
      <ReportNav />
      <div className="space-y-3">
        <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
        {q.loading && <Spinner />}
        {d && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <input className="input" style={{ maxWidth: 220 }} placeholder="お客様の名前で絞り込み" value={text} onChange={(e) => setText(e.target.value)} aria-label="お客様の名前で絞り込み" />
              {d.rank_thresholds.length > 0 && (
                <div className="flex flex-wrap gap-2" role="group" aria-label="ランクで絞り込み">
                  <button className={`btn btn-sm ${rank === "" ? "btn-primary" : ""}`} onClick={() => setRank("")} aria-pressed={rank === ""}>
                    すべて
                  </button>
                  {d.rank_thresholds.map((t) => (
                    <button key={t.label} className={`btn btn-sm ${rank === t.label ? "btn-primary" : ""}`} onClick={() => setRank(t.label)} aria-pressed={rank === t.label}>
                      {t.label}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="並び順">
                <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                  並び順
                </span>
                {SORTS.map((s) => (
                  <button key={s.key} className={`btn btn-sm ${sort === s.key ? "btn-primary" : ""}`} onClick={() => setSort(s.key)} aria-pressed={sort === s.key}>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
            <ReportTable label="お客様分析">
              <thead>
                <tr>
                  <th className="sticky-col">お客様</th>
                  <th>ランク</th>
                  <th className="r">キープ</th>
                  <th className="r">来店回数</th>
                  <th className="r">直近{d.rank_months}か月</th>
                  <th>最終来店日</th>
                  <th>初回来店日</th>
                  <th className="r">平均滞在</th>
                  <th className="r">単価</th>
                  <th className="r">会計</th>
                  <th className="r">現金</th>
                  <th className="r">カード</th>
                  <th className="r">売掛</th>
                  <th className="r">本指名</th>
                  <th className="r">場内指名</th>
                  <th className="r">同伴</th>
                  <th>担当</th>
                </tr>
              </thead>
              <tbody>
                <tr className="total-row">
                  <td className="sticky-col">合計 {d.totals.customers}名</td>
                  <td />
                  <td className="r">{cnt(d.totals.keep_count)}</td>
                  <td className="r">{cnt(d.totals.visits)}</td>
                  <td className="r">{cnt(d.totals.visits_recent)}</td>
                  <td />
                  <td />
                  <td />
                  <td className="r">{amt(d.totals.unit_price)}</td>
                  <td className="r">{amt(d.totals.total)}</td>
                  <td className="r">{amt(d.totals.cash)}</td>
                  <td className="r">{amt(d.totals.card)}</td>
                  <td className="r">{amt(d.totals.receivable)}</td>
                  <td className="r">{cnt(d.totals.nomination)}</td>
                  <td className="r">{cnt(d.totals.jounai)}</td>
                  <td className="r">{cnt(d.totals.douhan)}</td>
                  <td />
                </tr>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="sticky-col font-semibold">
                      {r.name}
                      {!r.own_store && (
                        <span className="badge ml-2" title="会社内で共有されている他店舗のお客様">
                          {r.store_name}
                        </span>
                      )}
                    </td>
                    <td>{r.rank ? <span className="badge">{r.rank}</span> : "--"}</td>
                    <td className="r">{r.keep_count > 0 ? `あり ${r.keep_count}` : "--"}</td>
                    <td className="r">{cnt(r.visits)}</td>
                    <td className="r">{cnt(r.visits_recent)}</td>
                    <td className="num">{r.last_visit ? fmtDate(r.last_visit) : "--"}</td>
                    <td className="num">{r.first_visit ? fmtDate(r.first_visit) : "--"}</td>
                    <td className="r">{stay(r.avg_minutes)}</td>
                    <td className="r">{amt(r.unit_price)}</td>
                    <td className="r font-semibold">{amt(r.total)}</td>
                    <td className="r">{amt(r.cash)}</td>
                    <td className="r">{amt(r.card)}</td>
                    <td className="r">{amt(r.receivable)}</td>
                    <td className="r">{cnt(r.nomination)}</td>
                    <td className="r">{cnt(r.jounai)}</td>
                    <td className="r">{cnt(r.douhan)}</td>
                    <td>{r.assigned_employee_name || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </ReportTable>
            {rows.length === 0 && <Empty>{d.rows.length === 0 ? "お客様が登録されていません。お客様の登録は「顧客・ボトルキープ」で行います。" : "条件に合うお客様はいません。"}</Empty>}
            <ReportNote>
              {d.note} ランクの対象は {fmtDate(d.rank_since)} の翌日〜{fmtDate(d.as_of)}（営業日）です。
              {d.sharing_enabled ? " 会社内の顧客共有がオンのため、共有されている他店舗のお客様も表示しています。" : ""}
              お客様の登録・キープの管理は「顧客・ボトルキープ」で行います。
            </ReportNote>
            <RankSettingsCard key={JSON.stringify(d.rank_thresholds)} ranks={d.rank_thresholds} months={d.rank_months} onSaved={() => q.reload(false)} />
          </>
        )}
      </div>
    </div>
  );
}

/** ランクのしきい値（店舗の設定）。変更は店舗設定の編集権限と再認証が必要 */
function RankSettingsCard({ ranks, months, onSaved }: { ranks: Rank[]; months: number; onSaved: () => void }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const canEdit = can("settings.edit");
  const [list, setList] = useState<{ label: string; min_visits: string }[]>(ranks.map((r) => ({ label: r.label, min_visits: String(r.min_visits) })));
  const [busy, setBusy] = useState(false);
  const set = (i: number, patch: Partial<{ label: string; min_visits: string }>) => setList((cur) => cur.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  async function save() {
    setBusy(true);
    try {
      const body = { customer_rank_thresholds: list.map((x) => ({ label: x.label.trim(), min_visits: Number(x.min_visits) })) };
      await withReauth(() => api("/api/store/settings", { method: "PUT", body }));
      toast.show({ message: "ランクの設定を保存しました" });
      onSaved();
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-2" aria-label="ランクの設定">
      <h2 className="font-bold">ランクの設定（直近 {months} か月の来店回数がこの回数以上のお客様に付けます）</h2>
      {list.length === 0 && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          ランクは設定されていません（表のランクは「--」になります）。
        </p>
      )}
      <div className="space-y-2">
        {list.map((x, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input className="input" style={{ width: 90 }} value={x.label} maxLength={6} disabled={!canEdit} onChange={(e) => set(i, { label: e.target.value })} aria-label={`ランク ${i + 1} の名前`} />
            <input className="input num" style={{ width: 100 }} inputMode="numeric" value={x.min_visits} disabled={!canEdit} onChange={(e) => set(i, { min_visits: e.target.value.replace(/[^\d]/g, "") })} aria-label={`ランク ${i + 1} の来店回数`} />
            <span className="text-sm">回以上</span>
            {canEdit && (
              <button className="btn btn-sm" onClick={() => setList((cur) => cur.filter((_, j) => j !== i))} aria-label={`ランク ${i + 1} を外す`}>
                外す
              </button>
            )}
          </div>
        ))}
      </div>
      {canEdit ? (
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" disabled={list.length >= 8} onClick={() => setList((cur) => [...cur, { label: "", min_visits: "" }])}>
            ランクを足す
          </button>
          <button className="btn btn-sm btn-primary" disabled={busy || list.some((x) => !x.label.trim() || x.min_visits === "")} onClick={save}>
            保存する
          </button>
        </div>
      ) : (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          変更には店舗設定の編集権限が必要です。
        </p>
      )}
    </section>
  );
}
