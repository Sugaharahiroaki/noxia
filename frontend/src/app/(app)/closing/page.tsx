"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { DailyPayoutBoard } from "@/components/DailyPayouts";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, NumberPad, PageHeader, SaveStatus, Spinner, Stat, type SaveState } from "@/components/ui";
import { api, ApiError, downloadFile, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtTime, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { BusinessDay, CashCurrent, DailyPayoutList, Employee } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

const DENOMS = [10000, 5000, 2000, 1000, 500, 100, 50, 10, 5, 1];
const KINDS: { kind: string; label: string; needsEmployee?: boolean; cap?: string }[] = [
  { kind: "expense", label: "現金経費（氷・備品など）" },
  { kind: "daily_pay", label: "日払い・前払い（従業員へ）", needsEmployee: true, cap: "payroll.pay" },
  { kind: "other_in", label: "その他入金" },
  { kind: "other_out", label: "その他出金" },
  { kind: "mid_collection", label: "途中回収（オーナー回収など）" },
  { kind: "float_adjust_in", label: "釣銭補充" },
];

export default function ClosingPage() {
  const { can, withReauth } = useSession();
  const { reload: reloadFloor } = useFloor();
  const cur = useLoad<CashCurrent>("/api/cash/current", [], { intervalMs: 30000 });
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const [tab, setTab] = useState<"today" | "history">("today");
  const [openFloat, setOpenFloat] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [mvModal, setMvModal] = useState(false);
  const [closeModal, setCloseModal] = useState(false);
  const [depositModal, setDepositModal] = useState<BusinessDay | null>(null);
  const [reviseModal, setReviseModal] = useState<BusinessDay | null>(null);
  const [reopenModal, setReopenModal] = useState<BusinessDay | null>(null);

  const day = cur.data?.day ?? null;
  useEffect(() => {
    if (cur.data && !cur.data.day && openFloat === "") setOpenFloat(String(toInt(cur.data.suggested_opening_float)));
  }, [cur.data, openFloat]);

  async function openDay() {
    setSave({ kind: "saving", label: "開店処理中…" });
    try {
      await api("/api/cash/open", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { opening_float: toInt(openFloat) } });
      setSave({ kind: "saved", label: "開店しました" });
      await cur.reload();
      reloadFloor();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  if (cur.loading) return <Spinner />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="開店・日締め・現金管理"
        subtitle={cur.data && <>本日の営業日: {fmtDate(cur.data.business_date_today)}（営業日切替時刻は店舗設定）</>}
        actions={
          <>
            <SaveStatus state={save} />
            <button className={`btn btn-sm ${tab === "today" ? "btn-primary" : ""}`} onClick={() => setTab("today")}>
              本日
            </button>
            <button className={`btn btn-sm ${tab === "history" ? "btn-primary" : ""}`} onClick={() => setTab("history")}>
              締め履歴
            </button>
            <button className="btn btn-sm" onClick={() => cur.reload(false)}>
              更新
            </button>
          </>
        }
      />
      <ErrorBox error={cur.error} onRetry={() => cur.reload(false)} />

      {tab === "history" && <History onDeposit={(d) => setDepositModal(d)} onRevise={(d) => setReviseModal(d)} canRevise={can("closing.edit_closed")} canExport={can("export.cash")} />}

      {tab === "today" && !day && cur.data && (
        <section className="card space-y-3 max-w-xl">
          <h2 className="font-bold text-lg">1. 開店: 釣銭準備金を登録</h2>
          {cur.data.previous_day && (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              前営業日（{fmtDate(cur.data.previous_day.date)}）の締めで残した翌日準備金: {yen(cur.data.previous_day.next_day_float)}
              {cur.data.suggested_from_previous_day && "（下に引き継いでいます）"}
            </p>
          )}
          <Field label="釣銭準備金（円）">
            <input className="input input-lg" inputMode="numeric" value={openFloat} onChange={(e) => setOpenFloat(e.target.value.replace(/[^\d]/g, ""))} />
          </Field>
          <button className="btn btn-primary btn-lg" onClick={openDay} disabled={save.kind === "saving"}>
            開店する
          </button>
          <p className="text-xs" style={{ color: "var(--color-muted)" }}>
            開店処理をしなくても入店・会計はできます（その場合は最初の操作時に営業日が自動で開き、準備金は前日の引継ぎまたは既定値になります）。
          </p>
        </section>
      )}

      {tab === "today" && !day && cur.data?.last_closed_day && (
        <section className="space-y-2">
          <h2 className="font-bold text-lg">直近の締め結果（営業日 {fmtDate(cur.data.last_closed_day.date)}）</h2>
          <DayPanel
            day={cur.data.last_closed_day}
            onMovement={() => {}}
            onClose={() => {}}
            onReload={() => cur.reload()}
            onDeposit={() => setDepositModal(cur.data!.last_closed_day)}
            onRevise={() => setReviseModal(cur.data!.last_closed_day)}
            onReopen={() => setReopenModal(cur.data!.last_closed_day)}
            canRevise={can("closing.edit_closed")}
            setSave={setSave}
          />
        </section>
      )}

      {reopenModal && (
        <ReopenModal
          day={reopenModal}
          onClose={() => setReopenModal(null)}
          onSubmit={async (reason) => {
            setSave({ kind: "saving", label: "再開中…" });
            try {
              await withReauth(() => api(`/api/cash/days/${reopenModal.id}/reopen`, { method: "POST", body: { reason } }));
              setSave({ kind: "saved", label: "営業日を再開しました" });
              setReopenModal(null);
              await cur.reload();
              reloadFloor();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}

      {tab === "today" && day && (
        <DayPanel
          day={day}
          onMovement={() => setMvModal(true)}
          onClose={() => setCloseModal(true)}
          onReload={() => cur.reload()}
          onDeposit={() => setDepositModal(day)}
          onRevise={() => setReviseModal(day)}
          canRevise={can("closing.edit_closed")}
          setSave={setSave}
        />
      )}

      {mvModal && day && (
        <MovementModal
          employees={employees.data ?? []}
          canPay={can("payroll.pay")}
          onClose={() => setMvModal(false)}
          onSubmit={async (body) => {
            setSave({ kind: "saving" });
            try {
              await api("/api/cash/movements", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved" });
              setMvModal(false);
              await cur.reload();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}

      {closeModal && day && (
        <CloseWizard
          day={day}
          canPayout={can("payroll.pay")}
          onCancel={async () => {
            setCloseModal(false);
            await cur.reload();
          }}
          onFinished={async () => {
            setCloseModal(false);
            await cur.reload();
            reloadFloor();
          }}
        />
      )}

      {depositModal && (
        <DepositModal
          day={depositModal}
          onClose={() => setDepositModal(null)}
          onSubmit={async (amount, note) => {
            setSave({ kind: "saving" });
            try {
              await api(`/api/cash/days/${depositModal.id}/bank-deposits`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { amount, note } });
              setSave({ kind: "saved", label: "銀行入金を記録しました" });
              setDepositModal(null);
              await cur.reload();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}

      {reviseModal && (
        <ReviseModal
          day={reviseModal}
          onClose={() => setReviseModal(null)}
          onSubmit={async (changes, reason) => {
            setSave({ kind: "saving" });
            try {
              await withReauth(() => api(`/api/cash/days/${reviseModal.id}/revise`, { method: "POST", body: { changes, reason } }));
              setSave({ kind: "saved", label: "修正を記録しました" });
              setReviseModal(null);
              await cur.reload();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}
    </div>
  );
}

/** 本日のまとめ: 売上 − 日払い − 経費 = 手残り（目安）。締め後は入金する現金も大きく出す。 */
function TodaySummary({ day }: { day: BusinessDay }) {
  const s = day.summary;
  const closed = day.status === "closed";
  const refunds = toInt(s.cash_refunds) + toInt(s.card_refunds);
  const row = (label: string, value: string, opts: { minus?: boolean; muted?: boolean } = {}) => (
    <div className="flex justify-between text-2xl num" style={opts.muted ? { color: "var(--color-muted)" } : undefined}>
      <span>{label}</span>
      <span>
        {opts.minus ? "−" : ""}
        {value}
      </span>
    </div>
  );
  return (
    <section className="card grid grid-cols-1 lg:grid-cols-2 gap-4" style={{ borderColor: "var(--color-accent)" }}>
      <div className="space-y-1">
        <h2 className="text-xl font-bold mb-1">本日のまとめ</h2>
        {row("売上", yen(s.sales_total))}
        {refunds > 0 && row("返金", yen(refunds), { minus: true })}
        {row("従業員へのお渡し（日払い）", yen(s.cash_out_daily_pay), { minus: true })}
        {row("現金経費", yen(s.cash_out_expense), { minus: true })}
        <div className="flex justify-between items-baseline pt-2 mt-1 border-t" style={{ borderColor: "var(--color-border)" }}>
          <span className="text-2xl font-bold">本日の手残り（目安）</span>
          <span className="font-bold num" style={{ fontSize: "2.6rem", color: "var(--color-ok)" }}>
            {yen(s.net_estimate)}
          </span>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          仕入・家賃などの固定費は含みません。ツケ（未収）{toInt(s.receivable_sales) > 0 ? ` ${yen(s.receivable_sales)}` : ""}は売上に含みます。
        </p>
      </div>
      <div className="space-y-1">
        <h2 className="text-xl font-bold mb-1">{closed ? "締めた結果" : "いまのレジ"}</h2>
        {closed ? (
          <>
            <div className="flex justify-between items-baseline">
              <span className="text-2xl font-bold">銀行に入金する現金</span>
              <span className="font-bold num" style={{ fontSize: "2.6rem", color: toInt(day.shortage_amount) > 0 ? "var(--color-danger)" : "var(--color-ok)" }}>
                {toInt(day.shortage_amount) > 0 ? `不足 ${yen(day.shortage_amount)}` : yen(day.set_aside_amount)}
              </span>
            </div>
            {row("翌日の釣銭として残す", yen(day.next_day_float), { muted: true })}
          </>
        ) : (
          <div className="flex justify-between items-baseline">
            <span className="text-2xl font-bold">レジにあるはずの現金</span>
            <span className="font-bold num" style={{ fontSize: "2.6rem" }}>
              {yen(s.theoretical_cash)}
            </span>
          </div>
        )}
        {row("カード売上（後日カード会社から入金）", yen(s.card_sales), { muted: true })}
        {toInt(s.receivable_sales) > 0 && row("ツケ（未収・あとで回収）", yen(s.receivable_sales), { muted: true })}
      </div>
    </section>
  );
}

function DayPanel({ day, onMovement, onClose, onReload, onDeposit, onRevise, onReopen, canRevise }: { day: BusinessDay; onMovement: () => void; onClose: () => void; onReload: () => void; onDeposit: () => void; onRevise: () => void; onReopen?: () => void; canRevise: boolean; setSave: (s: SaveState) => void }) {
  const s = day.summary;
  const closed = day.status === "closed";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`badge ${closed ? "badge-accent" : "badge-ok"}`}>
          営業日 {fmtDate(day.date)} ・ {closed ? "締め済" : "営業中"}
        </span>
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          開店 {fmtTime(day.opened_at)} {day.opened_by_name && `(${day.opened_by_name})`}
          {closed && ` ・ 締め ${fmtDateTime(day.closed_at)} (${day.closed_by_name})`}
        </span>
      </div>
      {!closed && (day.open_visits?.length ?? 0) > 0 && (
        <div className="card space-y-3" style={{ borderColor: "var(--color-danger)", borderWidth: 2, background: "rgba(240,91,91,0.12)" }} role="alert">
          <div className="text-2xl font-bold">⚠ 未会計の卓があるため、レジ締めできません</div>
          <div className="text-lg">会計するか、間違いなら伝票を取り消してください。</div>
          <div className="flex flex-wrap gap-2">
            {day.open_visits!.map((v) => (
              <Link key={v.visit_id} href={`/visits/${v.visit_id}`} className="btn btn-xl">
                {v.table_name}
                <span className="text-base font-normal ml-1">伝票#{v.slip_number}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
      {!closed && (
        <button
          className="btn btn-xl btn-primary w-full"
          style={{ minHeight: 100, fontSize: "1.8rem" }}
          onClick={onClose}
          disabled={(day.open_visits?.length ?? 0) > 0}
        >
          {(day.open_visits?.length ?? 0) > 0 ? `未会計が ${day.open_visits!.length}件 あるため締められません` : "レジ締めを始める"}
        </button>
      )}
      {!closed && day.precheck.filter((p) => p.code !== "open_visits").length > 0 && (
        <div className="card-2" style={{ borderColor: "var(--color-warn)" }}>
          <ul className="list-disc pl-5">
            {day.precheck
              .filter((p) => p.code !== "open_visits")
              .map((p) => (
                <li key={p.code}>{p.message}</li>
              ))}
          </ul>
          <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            これらがあってもレジ締めはできます。
          </p>
        </div>
      )}
      <TodaySummary day={day} />

      <details className="card-2">
        <summary className="cursor-pointer text-lg font-semibold">現金の内訳（理論現金の計算）</summary>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
        <Stat label="開始時釣銭" value={yen(s.opening_float)} sub={day.opening_float_carried ? "前営業日から引継ぎ" : undefined} />
        <Stat label="現金売上" value={yen(s.cash_sales)} />
        <Stat label="カード売上（現金に含めない）" value={yen(s.card_sales)} />
        <Stat label="未収計上" value={yen(s.receivable_sales)} />
        <Stat label="未収回収（現金）" value={yen(s.receivable_collected_cash)} />
        <Stat label="その他現金入金" value={yen(s.cash_in_other)} />
        <Stat label="現金経費" value={"−" + yen(s.cash_out_expense)} />
        <Stat label="日払い" value={"−" + yen(s.cash_out_daily_pay)} />
        <Stat label="その他出金" value={"−" + yen(s.cash_out_other)} />
        <Stat label="途中回収" value={"−" + yen(s.cash_out_mid_collection)} />
        <Stat label="現金返金" value={"−" + yen(s.cash_refunds)} />
        <Stat label="理論現金" value={yen(s.theoretical_cash)} accent="ok" sub="開始釣銭＋現金売上＋現金入金－返金－支出－日払い－回収" />
      </div>
      <div className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
        会計 {s.checkout_count} 件 ・ 客数 {s.guest_count} 名 ・ 売上合計（現金＋カード＋未収）{yen(s.sales_total)}
      </div>
      </details>

      {closed && (
        <div className="card space-y-2">
          <h2 className="font-bold">締めの詳細（数えた現金・差額）</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Stat label="実際の現金" value={yen(day.counted_cash)} />
            <Stat label="差額（実際－理論）" value={yen(day.difference, { sign: true })} accent={toInt(day.difference) === 0 ? "ok" : "danger"} sub={day.difference_reason || undefined} />
            <Stat label="翌日釣銭準備金" value={yen(day.next_day_float)} />
            {toInt(day.shortage_amount) > 0 ? (
              <Stat label="不足・補充必要額" value={yen(day.shortage_amount)} accent="danger" sub="実際の現金が翌日準備金に足りません" />
            ) : (
              <Stat label="入金用取り分け額" value={yen(day.set_aside_amount)} accent="ok" sub={`銀行入金済 ${yen(s.bank_deposits_total)}`} />
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn" onClick={onDeposit}>
              銀行入金を記録（売上とは別）
            </button>
            {canRevise && (
              <button className="btn" onClick={onRevise}>
                締め後の修正（履歴を残す）
              </button>
            )}
            {canRevise && onReopen && (
              <button className="btn" onClick={onReopen}>
                この営業日を再開する（締め前に戻す）
              </button>
            )}
          </div>
          {day.revisions.length > 0 && (
            <details className="text-sm">
              <summary>修正履歴 {day.revisions.length} 件</summary>
              {day.revisions.map((r) => (
                <div key={r.id} className="card-2 mt-1">
                  {fmtDateTime(r.changed_at)} {r.changed_by_name}: {r.reason}
                </div>
              ))}
            </details>
          )}
        </div>
      )}

      <div className="card space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="font-bold">現金の入出金</h2>
          {!closed && (
            <button className="btn btn-sm btn-primary" onClick={onMovement}>
              ＋ 入出金を登録
            </button>
          )}
        </div>
        {day.movements.length === 0 && <Empty>入出金はありません</Empty>}
        {day.movements.map((m) => (
          <div key={m.id} className="card-2 flex items-center gap-2" style={{ opacity: m.is_voided ? 0.5 : 1 }}>
            <span className="badge">{m.kind_label}</span>
            <span className="flex-1">
              {m.reason}
              {m.employee_name && ` ・ ${m.employee_name}`}
              <span className="text-xs ml-2" style={{ color: "var(--color-muted)" }}>
                {fmtTime(m.occurred_at)} {m.performed_by_name}
              </span>
              {m.is_voided && <span className="badge badge-danger ml-2">取消: {m.void_reason}</span>}
            </span>
            <span className="num font-bold">{m.kind === "other_in" || m.kind === "float_adjust_in" ? "+" : "−"}{yen(m.amount)}</span>
            {!m.is_voided && !closed && <VoidButton id={m.id} />}
          </div>
        ))}
      </div>

    </div>
  );
}

function VoidButton({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <>
      <button className="btn btn-sm btn-ghost" onClick={() => setOpen(true)}>
        取消
      </button>
      {open && (
        <ConfirmDialog
          title="入出金を取り消す"
          confirmLabel="取り消す"
          danger
          busy={busy}
          onCancel={() => setOpen(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api(`/api/cash/movements/${id}/void`, { method: "POST", body: { reason } });
              window.location.reload();
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="取消理由（必須）">
            <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <ErrorBox error={err} />
        </ConfirmDialog>
      )}
    </>
  );
}

function MovementModal({ employees, canPay, onClose, onSubmit }: { employees: Employee[]; canPay: boolean; onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const [kind, setKind] = useState("expense");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [emp, setEmp] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const def = KINDS.find((k) => k.kind === kind)!;
  return (
    <Modal
      title="現金の入出金を登録"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !amount || !reason.trim() || (def.needsEmployee && !emp)}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit({ kind, amount: toInt(amount), reason: reason.trim(), employee_id: emp });
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            登録する
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {KINDS.filter((k) => !k.cap || canPay).map((k) => (
            <button key={k.kind} className={`btn btn-sm ${kind === k.kind ? "btn-primary" : ""}`} onClick={() => setKind(k.kind)} aria-pressed={kind === k.kind}>
              {k.label}
            </button>
          ))}
        </div>
        {kind === "daily_pay" && (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            レジ現金からの日払いは、現金出金と従業員への支払記録を同時に1件だけ作ります（支払集計で二重に控除されません）。
          </p>
        )}
        {def.needsEmployee && (
          <Field label="従業員">
            <select className="input" value={emp ?? ""} onChange={(e) => setEmp(e.target.value ? Number(e.target.value) : null)}>
              <option value="">選択してください</option>
              {employees.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.display_name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="金額（円）">
          <input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
        <Field label="理由・内容（必須）">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

const DIFF_REASONS = ["お釣りの渡し間違い", "原因不明"];

/**
 * レジ締め（順番に進むだけ）:
 *  1. お渡しがまだの人（いれば。飛ばしても締められる）
 *  2. レジの現金を数えて合計を入れる（金種別は任意）
 *  3. ぴったりか確認（ずれていたら理由をボタンで）→ 確定
 *  4. お疲れさまでした ＋ 銀行に入金する現金
 */
function CloseWizard({ day, canPayout, onCancel, onFinished }: { day: BusinessDay; canPayout: boolean; onCancel: () => void; onFinished: () => void }) {
  const payouts = useLoad<DailyPayoutList>(canPayout ? `/api/staff/daily-payouts?date=${day.date}` : null);
  const pendingPayouts = (payouts.data?.rows ?? []).filter((r) => ["ready", "partial", "working", "missing_wage"].includes(r.status));
  const [step, setStep] = useState<"payout" | "count" | "check" | "done" | null>(null);
  const [withPayoutStep, setWithPayoutStep] = useState(false); // 最初に決めたら途中で変えない（ステップ数の表示がずれないように）
  const [fresh, setFresh] = useState<BusinessDay>(day);
  const [counted, setCounted] = useState("");
  const [useDenoms, setUseDenoms] = useState(false);
  const [denoms, setDenoms] = useState<Record<string, string>>({});
  const [reason, setReason] = useState<string | null>(null);
  const [otherReason, setOtherReason] = useState("");
  const [nextFloat, setNextFloat] = useState(String(toInt(day.opening_float)));
  const [editFloat, setEditFloat] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BusinessDay | null>(null);
  const idem = useRef(newIdempotencyKey());
  const toast = useToast();

  // 最初のステップ: お渡しがまだの人がいればそこから、いなければ現金を数えるところから
  useEffect(() => {
    if (step !== null) return;
    if (!canPayout) setStep("count");
    else if (!payouts.loading) {
      setWithPayoutStep(pendingPayouts.length > 0);
      setStep(pendingPayouts.length ? "payout" : "count");
    }
  }, [step, canPayout, payouts.loading, pendingPayouts.length]);

  // 数える前に最新の理論現金を取り直す（お渡しで出金が増えているため）
  async function goCount() {
    try {
      setFresh(await api<BusinessDay>(`/api/cash/days/${day.id}`));
    } catch {
      /* 取れなくても締め時にサーバーが再計算する */
    }
    setStep("count");
  }

  const denomTotal = DENOMS.reduce((s, d) => s + d * toInt(denoms[String(d)] ?? 0), 0);
  const countedN = useDenoms ? denomTotal : toInt(counted);
  const hasCount = useDenoms ? Object.values(denoms).some((v) => v !== "") : counted !== "";
  const theoretical = toInt(fresh.summary.theoretical_cash);
  const diff = countedN - theoretical;
  const reasonText = reason === "その他" ? otherReason.trim() : reason ?? "";
  const deposit = countedN - toInt(nextFloat);

  useEffect(() => {
    idem.current = newIdempotencyKey();
    setError(null);
  }, [counted, denoms, useDenoms, nextFloat, reason, otherReason]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { business_day_id: day.id, counted_cash: countedN, next_day_float: toInt(nextFloat), difference_reason: diff === 0 ? "" : reasonText };
      if (useDenoms) body.denominations = Object.fromEntries(DENOMS.map((d) => [String(d), toInt(denoms[String(d)] ?? 0)]));
      const r = await api<BusinessDay>("/api/cash/close", { method: "POST", idempotencyKey: idem.current, body });
      toast.hide(); // 締める前に出ていた「取り消す」は締めた後は使えないので消す
      setResult(r);
      setStep("done");
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) setError(`${e.message} もう一度押しても二重には締められません。`);
      else setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const stepNo = { payout: 1, count: withPayoutStep ? 2 : 1, check: withPayoutStep ? 3 : 2, done: 0 } as const;
  const totalSteps = withPayoutStep ? 3 : 2;
  const title =
    step === "done" ? "お疲れさまでした" : step === "payout" ? "お渡しがまだの人がいます" : step === "count" ? "レジの現金はいくら？" : step === "check" ? "確認して締める" : "レジ締め";

  return (
    <Modal title={step && step !== "done" ? `${title}（${stepNo[step]}/${totalSteps}）` : title} wide onClose={busy ? undefined : step === "done" ? onFinished : onCancel}>
      {step === null && <Spinner />}

      {step === "payout" && (
        <div className="space-y-3">
          <p className="text-lg">手渡ししたら「渡した」を押してください。渡さずに締めることもできます（あとで渡せます）。</p>
          <DailyPayoutBoard compact workDate={day.date} onChanged={() => payouts.reload()} />
          <div className="flex gap-3 justify-end pt-2">
            <button className="btn btn-xl" onClick={onCancel}>
              やめる
            </button>
            <button className="btn btn-xl btn-primary" style={{ minWidth: 260 }} onClick={goCount}>
              次へ（現金を数える）
            </button>
          </div>
        </div>
      )}

      {step === "count" && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-3">
              <div className="rounded-2xl px-4 py-3" style={{ background: "#11141b", border: "3px solid var(--color-accent)" }}>
                <div className="text-lg" style={{ color: "var(--color-muted)" }}>
                  数えた現金の合計
                </div>
                <div className="font-bold num text-right" style={{ fontSize: "3.4rem", lineHeight: 1.1 }}>
                  {hasCount ? yen(countedN) : "¥0"}
                </div>
              </div>
              <button className="btn w-full" onClick={() => setUseDenoms((v) => !v)}>
                {useDenoms ? "合計だけ入れる" : "お札・小銭の枚数で数える"}
              </button>
              {useDenoms && (
                <div className="grid grid-cols-2 gap-2">
                  {DENOMS.map((d) => (
                    <label key={d} className="flex items-center gap-2">
                      <span className="w-20 num text-right text-lg">{d.toLocaleString()}円</span>
                      <input className="input num text-xl" inputMode="numeric" placeholder="枚" value={denoms[String(d)] ?? ""} onChange={(e) => setDenoms({ ...denoms, [String(d)]: e.target.value.replace(/[^\d]/g, "") })} />
                    </label>
                  ))}
                </div>
              )}
            </div>
            {!useDenoms && <NumberPad value={counted} onChange={setCounted} onEnterKey={() => hasCount && setStep("check")} />}
          </div>
          <div className="flex gap-3 justify-end pt-2">
            <button className="btn btn-xl" onClick={onCancel}>
              やめる
            </button>
            <button className="btn btn-xl btn-primary" style={{ minWidth: 220 }} disabled={!hasCount} onClick={() => setStep("check")}>
              次へ
            </button>
          </div>
        </div>
      )}

      {step === "check" && (
        <div className="space-y-4">
          <div className="card-2 text-center" style={{ borderColor: diff === 0 ? "var(--color-ok)" : "var(--color-danger)", borderWidth: 2 }}>
            {diff === 0 ? (
              <div className="text-4xl font-bold" style={{ color: "var(--color-ok)" }}>
                ✓ ぴったりです
              </div>
            ) : (
              <>
                <div className="text-4xl font-bold num" style={{ color: "var(--color-danger)" }}>
                  {yen(Math.abs(diff))} {diff < 0 ? "足りません" : "多いです"}
                </div>
                <div className="text-lg num mt-1" style={{ color: "var(--color-muted)" }}>
                  数えた現金 {yen(countedN)} ／ レジにあるはずの現金 {yen(theoretical)}
                </div>
              </>
            )}
          </div>

          {diff !== 0 && (
            <div className="space-y-2">
              <div className="text-lg">理由を選んでください（記録されます）</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <button className="btn btn-lg" onClick={() => setStep("count")}>
                  数え直す
                </button>
                {[...DIFF_REASONS, "その他"].map((r) => (
                  <button key={r} className={`btn btn-lg ${reason === r ? "btn-primary" : ""}`} onClick={() => setReason(r)} aria-pressed={reason === r}>
                    {r}
                  </button>
                ))}
              </div>
              {reason === "その他" && <input className="input" autoFocus value={otherReason} onChange={(e) => setOtherReason(e.target.value)} placeholder="理由を入力" />}
            </div>
          )}

          <div className="card-2 space-y-2">
            <div className="flex items-center justify-between text-2xl num">
              <span>翌日の釣銭として残す</span>
              <span className="flex items-center gap-3">
                <strong>{yen(nextFloat)}</strong>
                <button className="btn btn-sm" onClick={() => setEditFloat((v) => !v)}>
                  {editFloat ? "決定" : "変える"}
                </button>
              </span>
            </div>
            {editFloat && (
              <div className="max-w-sm ml-auto">
                <NumberPad value={nextFloat} onChange={setNextFloat} quick={[30000, 50000, 70000, 100000]} />
              </div>
            )}
            <div className="flex items-baseline justify-between pt-2 border-t" style={{ borderColor: "var(--color-border)" }}>
              <span className="text-2xl font-bold">{deposit >= 0 ? "銀行に入金する現金" : "釣銭が足りません（補充が必要）"}</span>
              <span className="font-bold num" style={{ fontSize: "3rem", color: deposit >= 0 ? "var(--color-ok)" : "var(--color-danger)" }}>
                {yen(Math.abs(deposit))}
              </span>
            </div>
          </div>

          {error && (
            <p className="text-xl font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </p>
          )}
          <div className="flex gap-3 justify-end">
            <button className="btn btn-xl" onClick={() => setStep("count")} disabled={busy}>
              戻る
            </button>
            <button className="btn btn-xl btn-ok" style={{ minWidth: 280 }} disabled={busy || (diff !== 0 && !reasonText) || nextFloat === ""} onClick={confirm}>
              {busy ? "締めています…" : diff !== 0 && !reasonText ? "理由を選んでください" : "レジ締めを確定"}
            </button>
          </div>
        </div>
      )}

      {step === "done" && result && (
        <div className="space-y-4 text-center">
          <div className="text-2xl">営業日 {fmtDate(result.date)} のレジ締めが終わりました</div>
          <div>
            <div className="text-2xl" style={{ color: "var(--color-muted)" }}>
              {toInt(result.shortage_amount) > 0 ? "釣銭の補充が必要" : "銀行に入金する現金"}
            </div>
            <div className="font-bold num" style={{ fontSize: "5rem", lineHeight: 1.05, color: toInt(result.shortage_amount) > 0 ? "var(--color-danger)" : "var(--color-ok)" }}>
              {yen(toInt(result.shortage_amount) > 0 ? result.shortage_amount : result.set_aside_amount)}
            </div>
          </div>
          <div className="text-2xl num">
            本日の手残り（目安） <strong>{yen(result.summary.net_estimate)}</strong>
          </div>
          <div className="text-lg num" style={{ color: "var(--color-muted)" }}>
            翌日の釣銭 {yen(result.next_day_float)} を残してください
          </div>
          <button className="btn btn-xl btn-primary" style={{ minWidth: 280 }} onClick={onFinished}>
            閉じる
          </button>
        </div>
      )}
    </Modal>
  );
}

function DepositModal({ day, onClose, onSubmit }: { day: BusinessDay; onClose: () => void; onSubmit: (amount: number, note: string) => Promise<void> }) {
  const [amount, setAmount] = useState(String(toInt(day.set_aside_amount)));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`銀行入金を記録（営業日 ${fmtDate(day.date)}）`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !amount}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit(toInt(amount), note);
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            記録する
          </button>
        </>
      }
    >
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        銀行入金は売上として重複計上されません。取り分け額 {yen(day.set_aside_amount)} ・ 入金済 {yen(day.summary.bank_deposits_total)}
      </p>
      <Field label="入金額（円）">
        <input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
      </Field>
      <Field label="メモ（任意）">
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

function ReviseModal({ day, onClose, onSubmit }: { day: BusinessDay; onClose: () => void; onSubmit: (changes: Record<string, unknown>, reason: string) => Promise<void> }) {
  const [counted, setCounted] = useState(String(toInt(day.counted_cash)));
  const [nextFloat, setNextFloat] = useState(String(toInt(day.next_day_float)));
  const [diffReason, setDiffReason] = useState(day.difference_reason);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`締め後の修正（営業日 ${fmtDate(day.date)}）`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-danger"
            disabled={busy || !reason.trim()}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit({ counted_cash: toInt(counted), next_day_float: toInt(nextFloat), difference_reason: diffReason }, reason.trim());
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            修正を保存する
          </button>
        </>
      }
    >
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        修正前後の値と理由が履歴に残ります。
      </p>
      <div className="space-y-3">
        <Field label="実際の現金（円）">
          <input className="input" inputMode="numeric" value={counted} onChange={(e) => setCounted(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
        <Field label="翌日釣銭準備金（円）">
          <input className="input" inputMode="numeric" value={nextFloat} onChange={(e) => setNextFloat(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
        <Field label="差額理由">
          <input className="input" value={diffReason} onChange={(e) => setDiffReason(e.target.value)} />
        </Field>
        <Field label="修正理由（必須）">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function ReopenModal({ day, onClose, onSubmit }: { day: BusinessDay; onClose: () => void; onSubmit: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <ConfirmDialog
      title={`営業日 ${fmtDate(day.date)} を再開する`}
      message="締め前の状態に戻し、入店・会計・入出金を続けられるようにします。締めの数値は履歴に残り、再度日締めが必要です。パスワード再確認が必要です。"
      confirmLabel="再開する"
      danger
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        if (!reason.trim()) return;
        setBusy(true);
        try {
          await onSubmit(reason.trim());
        } catch {
          /* 表示済み */
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field label="再開の理由（必須）">
        <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
    </ConfirmDialog>
  );
}

function History({ onDeposit, onRevise, canRevise, canExport }: { onDeposit: (d: BusinessDay) => void; onRevise: (d: BusinessDay) => void; canRevise: boolean; canExport: boolean }) {
  const days = useLoad<BusinessDay[]>("/api/cash/days");
  const [err, setErr] = useState<unknown>(null);
  if (days.loading) return <Spinner />;
  return (
    <div className="space-y-2">
      <ErrorBox error={days.error || err} />
      {canExport && days.data && days.data.length > 0 && (
        <button
          className="btn btn-sm"
          onClick={() => downloadFile(`/api/exports/cash.csv?from=${days.data![days.data!.length - 1].date}&to=${days.data![0].date}`).catch(setErr)}
        >
          現金出納 CSV を出力
        </button>
      )}
      {days.data?.length === 0 && <Empty>締め履歴はありません</Empty>}
      <div className="scroll-x">
        <table className="table num">
          <thead>
            <tr>
              <th>営業日</th>
              <th>状態</th>
              <th>現金売上</th>
              <th>カード売上</th>
              <th>理論現金</th>
              <th>実際</th>
              <th>差額</th>
              <th>翌日釣銭</th>
              <th>取り分け / 不足</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {days.data?.map((d) => (
              <tr key={d.id}>
                <td>{fmtDate(d.date)}</td>
                <td>{d.status === "closed" ? "締め済" : "営業中"}</td>
                <td>{yen(d.cash_sales)}</td>
                <td>{yen(d.card_sales)}</td>
                <td>{yen(d.theoretical_cash)}</td>
                <td>{yen(d.counted_cash)}</td>
                <td style={{ color: toInt(d.difference) === 0 ? undefined : "var(--color-danger)" }}>{d.difference === null ? "—" : yen(d.difference, { sign: true })}</td>
                <td>{yen(d.next_day_float)}</td>
                <td>{toInt(d.shortage_amount) > 0 ? <span style={{ color: "var(--color-danger)" }}>不足 {yen(d.shortage_amount)}</span> : yen(d.set_aside_amount)}</td>
                <td className="whitespace-nowrap">
                  {d.status === "closed" && (
                    <>
                      <button className="btn btn-sm" onClick={() => onDeposit(d)}>
                        銀行入金
                      </button>{" "}
                      {canRevise && (
                        <button className="btn btn-sm" onClick={() => onRevise(d)}>
                          修正
                        </button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
