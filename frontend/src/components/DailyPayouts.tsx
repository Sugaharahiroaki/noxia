"use client";

import { useState } from "react";
import { api, ApiError, downloadFile, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtMinutes, fmtTime, toInt, yen } from "@/lib/format";
import { fmtElapsed, UNDO_REASON } from "@/lib/pos";
import type { DailyPayout, DailyPayoutList, PayoutDeductionOption, PayoutDeductionRow } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { useNow } from "@/lib/useServerClock";
import { useToast } from "./Toast";
import { ErrorBox, Modal, NumberPad, Spinner } from "./ui";

type PayResult = {
  payout: DailyPayout;
  cash_movement_id: number;
  amount: string;
  withholding_amount: string;
  other_deduction_amount: string;
  other_deductions: PayoutDeductionRow[];
};
type PayRequest = { amount?: number; deductions: string[]; expected: number };

/** 今渡す分の源泉所得税・その他控除（サーバー計算・店舗の控除項目は既定のチェックのまま）。未精算がなければ 0 */
function pendingDeductions(r: DailyPayout) {
  const t = r.tax;
  const active = t && toInt(r.remaining_gross) > 0;
  return { tax: active ? toInt(t.withholding_amount) : 0, other: active ? toInt(t.other_deduction_amount) : 0 };
}

/** 渡し済み・今回の控除の名前（「備品代・厚生費」など） */
function deductionLabels(r: DailyPayout) {
  const names = [...r.payments.flatMap((p) => p.deduction_items.map((d) => d.label)), ...(r.tax?.other_deductions.map((d) => d.label) ?? [])];
  return [...new Set(names)].join("・") || "その他控除";
}

/**
 * 店舗の控除項目（備品代など）のチェック。
 * お渡し額 = サーバーが出した「どれもチェックしないときの額」− チェックした項目の額（サーバーも同じ計算をして照合する）
 */
function useDeductionChecks(row: DailyPayout) {
  const options = (row.tax?.deduction_options ?? []).filter((o) => !o.done);
  const [checked, setChecked] = useState<string[]>(() => options.filter((o) => o.checked).map((o) => o.code));
  const picked = options.filter((o) => checked.includes(o.code));
  const net = row.tax ? toInt(row.tax.net_without_options) - picked.reduce((s, o) => s + toInt(o.amount), 0) : toInt(row.remaining_amount);
  const fixed = toInt(row.remaining_gross) > 0 ? (row.tax?.other_deductions ?? []).filter((d) => !d.optional) : [];
  return { options, checked, setChecked, picked, fixed, net };
}

function DeductionChecks({ options, checked, onChange, disabled }: { options: PayoutDeductionOption[]; checked: string[]; onChange: (codes: string[]) => void; disabled?: boolean }) {
  if (options.length === 0) return null;
  return (
    <div className="space-y-2 text-left">
      <div className="text-lg font-semibold">今回引くものにチェック</div>
      {options.map((o) => {
        const on = checked.includes(o.code);
        return (
          <label
            key={o.code}
            className="card-2 flex items-center gap-3 cursor-pointer text-xl"
            style={on ? { borderColor: "var(--color-warn)", borderWidth: 2 } : undefined}
          >
            <input
              type="checkbox"
              className="h-7 w-7 shrink-0"
              checked={on}
              disabled={disabled}
              onChange={(e) => onChange(e.target.checked ? [...checked, o.code] : checked.filter((c) => c !== o.code))}
            />
            <span className="flex-1">
              {o.label}
              <span className="text-base ml-1" style={{ color: "var(--color-muted)" }}>
                {Number(o.percent)}%
              </span>
            </span>
            <span className="num font-bold">−{yen(o.amount)}</span>
          </label>
        );
      })}
    </div>
  );
}

/** 源泉所得税の状態の小さな表示（営業中の画面なので短く） */
function TaxNote({ r }: { r: DailyPayout }) {
  const t = r.tax;
  if (!t || r.status === "absent") return null;
  if (t.status === "needs_review")
    return (
      <div className="text-base font-semibold" style={{ color: "var(--color-warn)" }} title={t.reasons.join("\n")}>
        ⚠ 税務区分の確認待ち（源泉所得税は引いていません。オーナーが確認します）
      </div>
    );
  if (t.status === "external")
    return (
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        給与（所得税は給与計算ソフトで計算）
      </div>
    );
  return null;
}

const STATUS: Record<DailyPayout["status"], { label: string; cls: string }> = {
  absent: { label: "未出勤", cls: "badge" },
  working: { label: "勤務中", cls: "badge badge-accent" },
  ready: { label: "お渡し待ち", cls: "badge badge-warn" },
  partial: { label: "一部渡し済み", cls: "badge badge-warn" },
  paid: { label: "✓ お渡し済み", cls: "badge badge-ok" },
  nothing: { label: "お渡しなし", cls: "badge" },
  missing_wage: { label: "時給未設定", cls: "badge badge-danger" },
};

/**
 * 本日の出勤・退勤とお渡し（日払い）。
 * 退勤するとお渡し額（時給×勤務時間＋確定バック）が出て、「渡した」でレジ出金と支払記録が同時に1件作られる。
 * compact: 日締め画面用（未出勤の人は出さない・出勤ボタンなし）
 */
export function DailyPayoutBoard({ compact, workDate, onChanged }: { compact?: boolean; workDate?: string; onChanged?: () => void }) {
  const q = useLoad<DailyPayoutList>(`/api/staff/daily-payouts${workDate ? `?date=${workDate}` : ""}`, [], { intervalMs: 30000 });
  const toast = useToast();
  const now = useNow(15000);
  const [busy, setBusy] = useState<number | null>(null);
  const [payTarget, setPayTarget] = useState<{ row: DailyPayout; greeting?: boolean } | null>(null);
  const [customTarget, setCustomTarget] = useState<DailyPayout | null>(null);
  const [undoTarget, setUndoTarget] = useState<DailyPayout | null>(null);
  const [err, setErr] = useState<unknown>(null);

  async function refresh() {
    await q.reload();
    onChanged?.();
  }

  async function clock(row: DailyPayout, action: "in" | "out" | "break") {
    setBusy(row.employee_id);
    setErr(null);
    try {
      if (action === "in") await api("/api/staff/shifts/clock-in", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { employee_id: row.employee_id } });
      else if (action === "out") await api(`/api/staff/shifts/${row.open_shift_id}/clock-out`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: {} });
      else await api(`/api/staff/shifts/${row.open_shift_id}/break`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { action: row.on_break ? "end" : "start" } });
      const list = await api<DailyPayoutList>(`/api/staff/daily-payouts${workDate ? `?date=${workDate}` : ""}`);
      q.setData(list);
      onChanged?.();
      if (action === "in") toast.show({ message: `${row.employee_name}さん 出勤しました` });
      if (action === "break") toast.show({ message: `${row.employee_name}さん ${row.on_break ? "休憩終了" : "休憩開始"}`, tone: "info" });
      if (action === "out") {
        const after = list.rows.find((r) => r.employee_id === row.employee_id);
        // 退勤したら、そのままお渡し額を出す
        if (after && (after.status === "ready" || after.status === "partial")) setPayTarget({ row: after, greeting: true });
        else toast.show({ message: `${row.employee_name}さん 退勤しました` });
      }
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  }

  async function pay(row: DailyPayout, req: PayRequest) {
    const body: Record<string, unknown> = { employee_id: row.employee_id, work_date: row.work_date, expected_amount: String(req.expected), deductions: req.deductions };
    if (req.amount !== undefined) body.amount = req.amount;
    const r = await api<PayResult>("/api/staff/daily-payouts/pay", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
    await refresh();
    toast.show({
      message:
        `${row.employee_name}さんに ${yen(r.amount)} 渡しました（レジから出金）` +
        (toInt(r.withholding_amount) > 0 ? `・源泉所得税 ${yen(r.withholding_amount)} は預り金` : "") +
        (r.other_deductions.length > 0 ? `・${r.other_deductions.map((d) => `${d.label} ${yen(d.amount)}`).join("・")} を差し引き` : ""),
      action: {
        label: "取り消す",
        onClick: async () => {
          try {
            await api(`/api/cash/movements/${r.cash_movement_id}/void`, { method: "POST", body: { reason: UNDO_REASON } });
            await refresh();
            toast.show({ message: "お渡しを取り消しました", tone: "info" });
          } catch (e) {
            toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
          }
        },
      },
    });
  }

  if (q.loading) return <Spinner label="出勤者を読み込み中…" />;
  const data = q.data;
  const rows = (data?.rows ?? []).filter((r) => !compact || r.status !== "absent");

  return (
    <div className="space-y-3">
      <ErrorBox error={q.error || err} onRetry={() => q.reload(false)} />
      {data && (
        <div className="flex flex-wrap gap-2 text-lg">
          <span className="badge" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
            お渡し合計 <strong className="num ml-1">{yen(data.totals.gross)}</strong>
          </span>
          <span className="badge badge-ok" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
            お渡し済み <strong className="num ml-1">{yen(data.totals.paid)}</strong>
          </span>
          {data.totals.unpaid_count > 0 && (
            <span className="badge badge-warn" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
              お渡し待ち {data.totals.unpaid_count}名 <strong className="num ml-1">{yen(data.totals.remaining)}</strong>
            </span>
          )}
          {data.totals.working_count > 0 && (
            <span className="badge badge-accent" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
              勤務中 {data.totals.working_count}名
            </span>
          )}
          {toInt(data.totals.withheld) > 0 && (
            <span className="badge" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
              源泉所得税（預り） <strong className="num ml-1">{yen(data.totals.withheld)}</strong>
            </span>
          )}
          {data.totals.tax_review_count > 0 && (
            <span className="badge badge-warn" style={{ fontSize: "1.05rem", padding: "6px 12px" }}>
              税務区分の確認待ち {data.totals.tax_review_count}名
            </span>
          )}
          {rows.some((r) => r.payments.length > 0) && (
            <button className="btn btn-sm ml-auto" onClick={() => downloadFile(`/api/accounting/daily-payouts.pdf?date=${data.work_date}`).catch(setErr)}>
              日払い精算書（PDF）
            </button>
          )}
        </div>
      )}
      {rows.length === 0 && (
        <p className="text-lg" style={{ color: "var(--color-muted)" }}>
          {compact ? "本日の出勤者はいません。" : "この店舗に配属されている従業員がいません。"}
        </p>
      )}
      <div className={`grid grid-cols-1 md:grid-cols-2 ${compact ? "" : "xl:grid-cols-3"} gap-3`}>
        {rows.map((r) => {
          const st = STATUS[r.status];
          const open = r.shifts.find((s) => !s.clock_out);
          const due = r.status === "ready" || r.status === "partial";
          return (
            <div
              key={r.employee_id}
              className="card space-y-2"
              style={due ? { borderColor: "var(--color-warn)", borderWidth: 2 } : r.status === "paid" ? { borderColor: "var(--color-ok)" } : undefined}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-3xl font-bold">{r.employee_name}</div>
                <span className={st.cls} style={{ fontSize: "1rem", padding: "6px 10px" }}>
                  {r.on_break ? "休憩中" : st.label}
                </span>
              </div>

              {r.status === "absent" && !compact && (
                <button className="btn btn-xl btn-ok w-full" disabled={busy !== null} onClick={() => clock(r, "in")}>
                  {busy === r.employee_id ? "記録中…" : "出勤"}
                </button>
              )}

              {r.working && open && (
                <>
                  <div className="text-lg num">
                    {fmtTime(open.clock_in)} から ・ <strong className="text-2xl">{fmtElapsed(open.clock_in, now)}</strong> 勤務中
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <button className="btn btn-lg" disabled={busy !== null} onClick={() => clock(r, "break")}>
                      {r.on_break ? "休憩終了" : "休憩開始"}
                    </button>
                    <button className="btn btn-lg btn-danger" disabled={busy !== null} onClick={() => clock(r, "out")}>
                      {busy === r.employee_id ? "記録中…" : "退勤"}
                    </button>
                  </div>
                </>
              )}

              {!r.working && r.status !== "absent" && (
                <div className="text-lg num space-y-0.5">
                  <div className="flex justify-between">
                    <span>
                      勤務 {fmtMinutes(r.worked_minutes)}
                      {r.shifts.length > 0 && (
                        <span className="text-base ml-1" style={{ color: "var(--color-muted)" }}>
                          （{r.shifts.map((s) => `${fmtTime(s.clock_in)}〜${fmtTime(s.clock_out)}`).join("、")}）
                        </span>
                      )}
                    </span>
                    <span>{yen(r.wage_amount)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>バック</span>
                    <span>{yen(r.back_confirmed)}</span>
                  </div>
                  {toInt(r.back_pending) > 0 && (
                    <div className="text-base" style={{ color: "var(--color-muted)" }}>
                      ※ 未確定のバック {yen(r.back_pending)}（未会計・ツケ回収待ち）は含みません
                    </div>
                  )}
                  {(toInt(r.withheld_amount) > 0 || pendingDeductions(r).tax > 0) && (
                    <div className="flex justify-between">
                      <span>源泉所得税（お店が預かる）</span>
                      <span>−{yen(toInt(r.withheld_amount) + pendingDeductions(r).tax)}</span>
                    </div>
                  )}
                  {(toInt(r.deducted_amount) > 0 || pendingDeductions(r).other > 0) && (
                    <div className="flex justify-between">
                      <span>{deductionLabels(r)}</span>
                      <span>−{yen(toInt(r.deducted_amount) + pendingDeductions(r).other)}</span>
                    </div>
                  )}
                  {toInt(r.paid_amount) > 0 && (
                    <div className="flex justify-between" style={{ color: "var(--color-ok)" }}>
                      <span>お渡し済み</span>
                      <span>{yen(r.paid_amount)}</span>
                    </div>
                  )}
                  <TaxNote r={r} />
                </div>
              )}

              {r.status === "missing_wage" && (
                <p className="text-lg font-bold" style={{ color: "var(--color-danger)" }}>
                  時給が未設定のため計算できません。オーナーが「従業員管理」で時給を設定してください。
                </p>
              )}

              {due && (
                <>
                  <div className="flex items-baseline justify-between pt-1">
                    <span className="text-xl">お渡し</span>
                    <span className="font-bold num" style={{ fontSize: "2.6rem", lineHeight: 1.1, color: "var(--color-warn)" }}>
                      {yen(r.remaining_amount)}
                    </span>
                  </div>
                  <button className="btn btn-xl btn-ok w-full" onClick={() => setPayTarget({ row: r })}>
                    渡した（{yen(r.remaining_amount)}）
                  </button>
                  <button className="btn btn-sm btn-ghost w-full" onClick={() => setCustomTarget(r)}>
                    金額を変えて渡す
                  </button>
                </>
              )}

              {r.status === "paid" && (
                <div className="flex items-center justify-between">
                  <span className="text-2xl font-bold num" style={{ color: "var(--color-ok)" }}>
                    ✓ {yen(r.paid_amount)}
                    <span className="text-base font-normal ml-2">{r.payments.map((p) => fmtTime(p.paid_at)).join("・")}</span>
                  </span>
                  <span className="flex gap-1">
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => downloadFile(`/api/accounting/daily-payouts.pdf?date=${r.work_date}&employee=${r.employee_id}`).catch(setErr)}
                    >
                      精算書
                    </button>
                    <button className="btn btn-sm btn-ghost" onClick={() => setUndoTarget(r)}>
                      取り消す
                    </button>
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {data && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          {data.notice} 渡した金額はレジの現金出金（日払い）として記録され、レジ締めの理論現金から差し引かれます。
        </p>
      )}

      {payTarget && (
        <PayConfirm
          row={payTarget.row}
          greeting={payTarget.greeting}
          onClose={() => setPayTarget(null)}
          onPay={async (req) => {
            await pay(payTarget.row, req);
            setPayTarget(null);
          }}
          onReload={async () => {
            await q.reload();
            setPayTarget(null);
          }}
        />
      )}
      {customTarget && (
        <CustomPay
          row={customTarget}
          onClose={() => setCustomTarget(null)}
          onPay={async (req) => {
            await pay(customTarget, req);
            setCustomTarget(null);
          }}
        />
      )}
      {undoTarget && (
        <Modal
          title={`${undoTarget.employee_name}さんへのお渡しを取り消す`}
          onClose={() => setUndoTarget(null)}
          footer={
            <>
              <button className="btn btn-xl" onClick={() => setUndoTarget(null)}>
                やめる
              </button>
              <button
                className="btn btn-xl btn-danger"
                onClick={async () => {
                  try {
                    for (const p of undoTarget.payments) {
                      if (p.cash_movement_id) await api(`/api/cash/movements/${p.cash_movement_id}/void`, { method: "POST", body: { reason: "渡し済みの取り消し（渡していなかった）" } });
                    }
                    await refresh();
                    toast.show({ message: `${undoTarget.employee_name}さんのお渡しを取り消しました`, tone: "info" });
                  } catch (e) {
                    toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
                  } finally {
                    setUndoTarget(null);
                  }
                }}
              >
                取り消す
              </button>
            </>
          }
        >
          <p className="text-xl">
            {yen(undoTarget.paid_amount)} の「渡し済み」とレジの出金記録を取り消します（履歴は残ります）。実際には渡していなかった場合だけ使ってください。
          </p>
        </Modal>
      )}
    </div>
  );
}

function PayConfirm({ row, greeting, onClose, onPay, onReload }: { row: DailyPayout; greeting?: boolean; onClose: () => void; onPay: (req: PayRequest) => Promise<void>; onReload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; changed: boolean } | null>(null);
  const d = useDeductionChecks(row);
  const tax = pendingDeductions(row).tax;
  return (
    <Modal
      title={greeting ? `${row.employee_name}さん、お疲れさまでした` : `${row.employee_name}さんに渡す`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            {greeting ? "あとで渡す" : "やめる"}
          </button>
          {error?.changed ? (
            <button className="btn btn-xl btn-primary" onClick={onReload}>
              最新の金額を見る
            </button>
          ) : (
            <button
              className="btn btn-xl btn-ok"
              style={{ minWidth: 220 }}
              disabled={busy || d.net <= 0}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await onPay({ deductions: d.checked, expected: d.net });
                } catch (e) {
                  setError({ message: errorMessage(e), changed: e instanceof ApiError && e.code === "amount_changed" });
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "記録中…" : "渡した"}
            </button>
          )}
        </>
      }
    >
      <div className="text-center space-y-2">
        <div className="text-xl">本日のお渡し</div>
        <div className="font-bold num" style={{ fontSize: "4.5rem", lineHeight: 1, color: "var(--color-warn)" }}>
          {yen(d.net)}
        </div>
        <div className="text-lg num" style={{ color: "var(--color-muted)" }}>
          勤務 {fmtMinutes(row.worked_minutes)} {yen(row.wage_amount)} ＋ バック {yen(row.back_confirmed)}
          {toInt(row.settled_amount) > 0 && ` − 精算済み ${yen(row.settled_amount)}`}
          {tax > 0 && ` − 源泉所得税 ${yen(tax)}`}
          {[...d.fixed, ...d.picked].map((x) => ` − ${x.label} ${yen(x.amount)}`).join("")}
        </div>
        <DeductionChecks options={d.options} checked={d.checked} onChange={d.setChecked} disabled={busy} />
        {d.net <= 0 && (
          <p className="text-xl font-bold" style={{ color: "var(--color-danger)" }}>
            差し引くと渡す額が0円以下になります。チェックを外してください。
          </p>
        )}
        {tax > 0 && (
          <div className="text-base" style={{ color: "var(--color-muted)" }}>
            源泉所得税はお店が預かって税務署に納めます（本人に渡す額から差し引き済み）。
          </div>
        )}
        <TaxNote r={row} />
        <div className="text-base" style={{ color: "var(--color-muted)" }}>
          レジの現金から出して手渡ししたら「渡した」を押してください。レジ締めの現金出金に自動で入ります。
        </div>
        {error && (
          <p className="text-xl font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error.message}
          </p>
        )}
      </div>
    </Modal>
  );
}

function CustomPay({ row, onClose, onPay }: { row: DailyPayout; onClose: () => void; onPay: (req: PayRequest) => Promise<void> }) {
  const d = useDeductionChecks(row);
  // 金額を打つまでは、チェックの付け外しに合わせて未渡しの全額を出しておく
  const [typed, setTyped] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const max = d.net;
  const value = typed ?? String(Math.max(max, 0));
  const n = toInt(value);
  const tax = pendingDeductions(row).tax;
  const fixedNote = [...(tax > 0 ? [`源泉所得税 ${yen(tax)}`] : []), ...d.fixed.map((x) => `${x.label} ${yen(x.amount)}`)].join("・");
  return (
    <Modal
      title={`${row.employee_name}さんに金額を変えて渡す`}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-xl btn-ok"
            disabled={busy || n <= 0 || n > max}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onPay({ amount: n, deductions: d.checked, expected: d.net });
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "記録中…" : `${yen(n)} 渡した`}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <div className="text-lg">未渡し {yen(max)}</div>
          {fixedNote && (
            <div className="text-sm" style={{ color: "var(--color-muted)" }}>
              （{fixedNote} を差し引いた額）
            </div>
          )}
          <div className="text-5xl font-bold num text-right">{yen(n)}</div>
          {n > max && <p className="font-bold" style={{ color: "var(--color-danger)" }}>未渡しの額より多くは渡せません。</p>}
          {n > 0 && n < max && <p style={{ color: "var(--color-muted)" }}>残り {yen(max - n)} は未渡しとして残ります（あとで渡せます）。</p>}
          <DeductionChecks options={d.options} checked={d.checked} onChange={d.setChecked} disabled={busy} />
          {d.picked.length > 0 && (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              チェックした項目は、この支払いでその日の分をまとめて差し引きます。
            </p>
          )}
          {error && <p className="font-bold" style={{ color: "var(--color-danger)" }}>{error}</p>}
        </div>
        <NumberPad value={value} onChange={setTyped} />
      </div>
    </Modal>
  );
}
