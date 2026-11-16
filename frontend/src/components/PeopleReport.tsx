"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { MonthPicker } from "@/components/common";
import { useBusinessDate } from "@/components/DateRange";
import { ReportNav, ReportNote, ReportTable } from "@/components/ReportKit";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { payCell, payGroups, type PayGroup, type PayRow, type PeopleReport } from "@/lib/payColumns";
import { useSession } from "@/lib/session";
import { useToast } from "./Toast";

/** 月と、再認証つきの読み込み（全員分の報酬は、既存の支払集計と同じくパスワードの再確認が要る） */
export function useMonthReport<T>(path: (month: string) => string, initialMonth: string | null = null) {
  const { withReauth } = useSession();
  const bd = useBusinessDate();
  const [month, setMonth] = useState<string | null>(initialMonth);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (bd && month === null) setMonth(bd.slice(0, 7));
  }, [bd, month]);
  const load = useCallback(async () => {
    if (!month) return;
    setLoading(true);
    try {
      setData(await withReauth(() => api<T>(path(month))));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [month]);
  useEffect(() => {
    load();
  }, [load]);
  return { month, setMonth, data, error, loading, reload: load };
}

/** 多段の見出し（グループ → 列） */
export function PayHead({ groups, first }: { groups: PayGroup[]; first: string }) {
  return (
    <thead>
      <tr className="group-head">
        <th rowSpan={2} className="sticky-col">
          {first}
        </th>
        {groups.map((g) => (
          <th key={g.key} colSpan={g.cols.length}>
            {g.label}
          </th>
        ))}
      </tr>
      <tr>
        {groups.flatMap((g) =>
          g.cols.map((c) => (
            <th key={`${g.key}-${c.key}`} className="r">
              {c.label}
            </th>
          )),
        )}
      </tr>
    </thead>
  );
}

export function PayCells({ row, groups }: { row: PayRow; groups: PayGroup[] }) {
  return (
    <>
      {groups.flatMap((g) =>
        g.cols.map((c) => (
          <td key={`${g.key}-${c.key}`} className={`r ${c.strong ? "font-semibold" : ""}`}>
            {payCell(row, c)}
          </td>
        )),
      )}
    </>
  );
}

/** 列（グループ）の表示切替 */
export function GroupToggles({ all, hidden, onChange }: { all: PayGroup[]; hidden: Set<string>; onChange: (h: Set<string>) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="列の表示">
      <span className="text-sm" style={{ color: "var(--color-muted)" }}>
        列の表示
      </span>
      {all.map((g) => {
        const on = !hidden.has(g.key);
        return (
          <button
            key={g.key}
            className={`btn btn-sm ${on ? "btn-primary" : ""}`}
            aria-pressed={on}
            onClick={() => {
              const next = new Set(hidden);
              if (on) next.add(g.key);
              else next.delete(g.key);
              onChange(next);
            }}
          >
            {on ? "✓ " : ""}
            {g.label}
          </button>
        );
      })}
    </div>
  );
}

/** キャスト集計／スタッフ集計（月次。1 行 = 1 人。合計行は先頭） */
export function PeopleReportPage({ kind }: { kind: "cast" | "staff" }) {
  const cast = kind === "cast";
  const toast = useToast();
  const { can, withReauth } = useSession();
  const q = useMonthReport<PeopleReport>((m) => `/api/reports/${kind}?month=${m}`);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const d = q.data;
  const withholding = !!d?.settings.withholding_enabled;
  const all = payGroups({ cast, withholding });
  const groups = payGroups({ cast, withholding, hidden });

  return (
    <div>
      <PageHeader
        title={cast ? "キャスト集計（売上と報酬）" : "スタッフ集計（報酬）"}
        subtitle="報酬は既存の計算結果（時給×勤務時間、確定したバック、渡した日払い）を集めたものです。この画面では金額を変更しません。"
        actions={
          <>
            {q.month && <MonthPicker value={q.month} onChange={q.setMonth} />}
            {can("export.payroll") && q.month && (
              <button
                className="btn btn-sm"
                onClick={async () => {
                  try {
                    await withReauth(() => downloadFile(`/api/reports/people-${kind}.csv?month=${q.month}`));
                  } catch (e) {
                    toast.show({ message: errorMessage(e), tone: "error" });
                  }
                }}
              >
                CSV 出力
              </button>
            )}
          </>
        }
      />
      <ReportNav />
      <ErrorBox error={q.error} onRetry={q.reload} />
      {q.loading && <Spinner />}
      {d && !q.loading && (
        <div className="space-y-3">
          <GroupToggles all={all} hidden={hidden} onChange={setHidden} />
          <ReportTable label={cast ? "キャスト集計" : "スタッフ集計"}>
            <PayHead groups={groups} first="名前" />
            <tbody>
              <tr className="total-row">
                <td className="sticky-col">合計 {d.rows.length}名</td>
                <PayCells row={d.totals} groups={groups} />
              </tr>
              {d.rows.map((r) => (
                <tr key={r.employee_id} style={r.is_active ? undefined : { opacity: 0.55 }}>
                  <td className="sticky-col">
                    <Link href={`/reports/cast/${r.employee_id}?month=${d.month}`} className="underline font-semibold">
                      {r.employee_name}
                    </Link>
                    {!r.is_active && <span className="badge ml-1">退職</span>}
                  </td>
                  <PayCells row={r} groups={groups} />
                </tr>
              ))}
            </tbody>
          </ReportTable>
          {d.rows.length === 0 && <Empty>対象の人がいません。</Empty>}
          <ReportNote>
            総支給額 = 時間報酬 ＋ バック計（ボーナス・マイナスの調整を含む）。残り支給額 = 総支給額 − 日払い − 控除{withholding ? " − 源泉所得税" : ""}。
            {cast && "給率 = 総支給額 ÷ 呼び小計。呼び小計・本計は、指名（本指名・場内）のある伝票の小計・本計（複数人のときの分け方は伝票一覧の下の設定）。"}
            ボーナス・マイナスは手入力のバック調整（正・負）です。遅刻・欠勤を分けた項目や、手当としての記録はありません。法定控除・割増賃金は含みません。
            名前を押すと、その人の報酬支払明細と日別表を開きます。
          </ReportNote>
        </div>
      )}
    </div>
  );
}
