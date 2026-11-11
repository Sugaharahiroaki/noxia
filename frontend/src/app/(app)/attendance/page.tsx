"use client";

import { useEffect, useState } from "react";
import { DailyPayoutBoard } from "@/components/DailyPayouts";
import { DateRangeBar, useDateRange } from "@/components/DateRange";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Stat, type SaveState } from "@/components/ui";
import { api, downloadFile, newIdempotencyKey } from "@/lib/api";
import { addDays, fmtDate, fmtDateTime, fmtMinutes, fmtTime, monthStart, todayJst, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DailyPayout, Employee } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Shift = {
  id: number;
  employee: number;
  employee_name: string;
  business_date: string;
  clock_in: string;
  clock_out: string | null;
  note: string;
  breaks: { id: number; started_at: string; ended_at: string | null }[];
  corrections: { id: number; reason: string; corrected_by_name: string; corrected_at: string }[];
  worked_minutes: number;
};
type ShiftsRes = { from: string; to: string; shifts: Shift[]; open_shifts: Shift[] };
type BackEntry = { id: number; employee_name: string; visit_slip_number: number | null; line_name: string | null; kind: string; business_date: string; amount: string; status: string; status_label: string; reason: string };
type Statement = {
  employee_id: number;
  employee_name: string;
  worked_minutes: number;
  wage_amount: string;
  back_amount: string;
  gross_amount: string;
  paid_amount: string;
  balance_amount: string;
  missing_wage_setting: boolean;
  shifts: { shift_id: number; business_date: string; minutes: number; hourly_wage: string | null; amount: string; open: boolean }[];
  notice: string;
};
type MyData = {
  employee: Employee | null;
  message?: string;
  from: string;
  to: string;
  open_shift: Shift | null;
  shifts: Shift[];
  backs: BackEntry[];
  back_total_confirmed: string;
  statement?: Statement;
  today_payout?: DailyPayout;
};
type PayPeriod = { id: number; start_date: string; end_date: string; pay_date: string | null; status: string; closed_at: string | null; note: string; statements: { id: number; employee_name: string; worked_minutes: number; wage_amount: string; back_amount: string; gross_amount: string; paid_amount: string; balance_amount: string }[] };

export default function AttendancePage() {
  const { can, me } = useSession();
  const manage = can("attendance.manage");
  const canPayout = can("payroll.pay") && manage;
  const [tab, setTab] = useState<"today" | "me" | "all" | "backs" | "pay">(canPayout ? "today" : manage ? "all" : "me");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  return (
    <div className="space-y-4">
      <PageHeader
        title={tab === "today" ? "本日の出勤・お渡し" : "勤怠・支払集計"}
        subtitle={
          tab === "today"
            ? "出勤・退勤を押すと、退勤した人のお渡し額（時給×勤務時間＋その日の確定バック）が出ます。手渡ししたら「渡した」を押してください。"
            : "勤務時間×時給と確定バックに基づく支払集計です。所得税・社会保険・割増賃金などの法定控除は含みません（正式な給与計算ではありません）。"
        }
        actions={
          <>
            <SaveStatus state={save} />
            {canPayout && (
              <button className={`btn btn-lg ${tab === "today" ? "btn-primary" : ""}`} onClick={() => setTab("today")}>
                本日の出勤・お渡し
              </button>
            )}
            {me?.employee && (
              <button className={`btn btn-sm ${tab === "me" ? "btn-primary" : ""}`} onClick={() => setTab("me")}>
                自分の勤怠・成績
              </button>
            )}
            {manage && (
              <button className={`btn btn-sm ${tab === "all" ? "btn-primary" : ""}`} onClick={() => setTab("all")}>
                全員の勤怠
              </button>
            )}
            {can("backs.view_all") && (
              <button className={`btn btn-sm ${tab === "backs" ? "btn-primary" : ""}`} onClick={() => setTab("backs")}>
                バック集計
              </button>
            )}
            {can("payroll.view_all") && (
              <button className={`btn btn-sm ${tab === "pay" ? "btn-primary" : ""}`} onClick={() => setTab("pay")}>
                期間の集計（確認用）
              </button>
            )}
          </>
        }
      />
      {tab === "today" && canPayout && <DailyPayoutBoard />}
      {tab === "me" && <MyTab setSave={setSave} />}
      {tab === "all" && manage && <AllShiftsTab setSave={setSave} />}
      {tab === "backs" && <BacksTab />}
      {tab === "pay" && <PayTab setSave={setSave} />}
    </div>
  );
}

function ClockButtons({ shift, employeeId, onDone, setSave }: { shift: Shift | null; employeeId: number | "me"; onDone: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const [busy, setBusy] = useState(false);
  const onBreak = shift?.breaks.some((b) => !b.ended_at);
  async function run(path: string, body: unknown, label: string) {
    setBusy(true);
    setSave({ kind: "saving", label });
    try {
      await api(path, { method: "POST", idempotencyKey: newIdempotencyKey(), body });
      setSave({ kind: "saved" });
      await onDone();
    } catch (e) {
      setSave({ kind: "error", error: e });
    } finally {
      setBusy(false);
    }
  }
  if (!shift)
    return (
      <button className="btn btn-ok btn-lg" disabled={busy} onClick={() => run("/api/staff/shifts/clock-in", { employee_id: employeeId }, "出勤を記録中…")}>
        出勤
      </button>
    );
  return (
    <div className="flex flex-wrap gap-2">
      <button className="btn btn-lg" disabled={busy} onClick={() => run(`/api/staff/shifts/${shift.id}/break`, { action: onBreak ? "end" : "start" }, "休憩を記録中…")}>
        {onBreak ? "休憩終了" : "休憩開始"}
      </button>
      <button className="btn btn-danger btn-lg" disabled={busy} onClick={() => run(`/api/staff/shifts/${shift.id}/clock-out`, {}, "退勤を記録中…")}>
        退勤
      </button>
    </div>
  );
}

function MyTab({ setSave }: { setSave: (s: SaveState) => void }) {
  const [range, setRange, bd] = useDateRange();
  const from = range?.from ?? "";
  const to = range?.to ?? "";
  const q = useLoad<MyData>(range ? `/api/staff/me?from=${from}&to=${to}` : null);
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  // 営業日の取得 → 自分のデータの取得、の順に読み込むので、揃うまでは読み込み中を出す
  if (!range || q.loading || !q.data) return <Spinner />;
  const d = q.data;
  if (!d.employee) return <div className="card">{d.message ?? "従業員情報がありません。"}</div>;
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-4">
        <div>
          <div className="font-bold text-lg">{d.employee.display_name}</div>
          {d.open_shift ? (
            <div className="text-sm" style={{ color: "var(--color-ok)" }}>
              出勤中（{fmtTime(d.open_shift.clock_in)} から・{fmtMinutes(d.open_shift.worked_minutes)}）{d.open_shift.breaks.some((b) => !b.ended_at) && " ・ 休憩中"}
            </div>
          ) : (
            <div className="text-sm" style={{ color: "var(--color-muted)" }}>
              退勤中
            </div>
          )}
        </div>
        <div className="ml-auto">
          <ClockButtons shift={d.open_shift} employeeId="me" onDone={() => q.reload()} setSave={setSave} />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <DateRangeBar range={range} onChange={setRange} businessDate={bd} />
      </div>
      {d.today_payout && d.today_payout.status !== "absent" && (
        <div className="card flex flex-wrap items-center gap-4" style={{ borderColor: d.today_payout.status === "paid" ? "var(--color-ok)" : "var(--color-warn)" }}>
          <div>
            <div className="text-lg">本日のお渡し{d.today_payout.working && "（退勤したら確定）"}</div>
            <div className="text-sm num" style={{ color: "var(--color-muted)" }}>
              勤務 {fmtMinutes(d.today_payout.worked_minutes)} {yen(d.today_payout.wage_amount)} ＋ バック {yen(d.today_payout.back_confirmed)}
            </div>
          </div>
          <div className="ml-auto text-right">
            <div className="text-4xl font-bold num">{yen(d.today_payout.gross_amount)}</div>
            <div className="text-lg font-bold" style={{ color: d.today_payout.status === "paid" ? "var(--color-ok)" : "var(--color-warn)" }}>
              {d.today_payout.status === "paid" ? "✓ 受け取り済み" : toInt(d.today_payout.paid_amount) > 0 ? `受け取り済み ${yen(d.today_payout.paid_amount)}` : d.today_payout.working ? "勤務中" : "未受け取り"}
            </div>
          </div>
        </div>
      )}
      {d.statement && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <Stat label="勤務時間" value={fmtMinutes(d.statement.worked_minutes)} />
          <Stat label="時給分" value={yen(d.statement.wage_amount)} sub={d.statement.missing_wage_setting ? "時給未設定の勤務あり" : undefined} accent={d.statement.missing_wage_setting ? "warn" : undefined} />
          <Stat label="確定バック" value={yen(d.statement.back_amount)} />
          <Stat label="支給対象額" value={yen(d.statement.gross_amount)} />
          <Stat label="支払済 / 残額" value={`${yen(d.statement.paid_amount)} / ${yen(d.statement.balance_amount)}`} />
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="card">
          <h2 className="font-bold mb-2">勤怠</h2>
          <ShiftTable shifts={d.shifts} />
        </div>
        <div className="card">
          <h2 className="font-bold mb-2">バック（確定 {yen(d.back_total_confirmed)}）</h2>
          <BackTable backs={d.backs} />
        </div>
      </div>
    </div>
  );
}

function ShiftTable({ shifts, onCorrect }: { shifts: Shift[]; onCorrect?: (s: Shift) => void }) {
  if (shifts.length === 0) return <Empty>勤怠はありません</Empty>;
  return (
    <div className="scroll-x">
      <table className="table num">
        <thead>
          <tr>
            <th>営業日</th>
            <th>従業員</th>
            <th>出勤</th>
            <th>退勤</th>
            <th>休憩</th>
            <th>勤務</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {shifts.map((s) => (
            <tr key={s.id}>
              <td>{fmtDate(s.business_date)}</td>
              <td>{s.employee_name}</td>
              <td>{fmtDateTime(s.clock_in)}</td>
              <td>{s.clock_out ? fmtDateTime(s.clock_out) : <span className="badge badge-ok">勤務中</span>}</td>
              <td>{s.breaks.length ? s.breaks.map((b) => `${fmtTime(b.started_at)}-${b.ended_at ? fmtTime(b.ended_at) : "…"}`).join(", ") : "—"}</td>
              <td>
                {fmtMinutes(s.worked_minutes)}
                {s.corrections.length > 0 && <span className="badge ml-1" title={s.corrections.map((c) => c.reason).join(" / ")}>修正{s.corrections.length}</span>}
              </td>
              <td>
                {onCorrect && (
                  <button className="btn btn-sm" onClick={() => onCorrect(s)}>
                    修正
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BackTable({ backs }: { backs: BackEntry[] }) {
  if (backs.length === 0) return <Empty>バックはありません</Empty>;
  return (
    <div className="scroll-x">
      <table className="table num">
        <thead>
          <tr>
            <th>営業日</th>
            <th>従業員</th>
            <th>内容</th>
            <th>金額</th>
            <th>状態</th>
          </tr>
        </thead>
        <tbody>
          {backs.map((b) => (
            <tr key={b.id} style={{ opacity: b.status === "reversed" ? 0.5 : 1 }}>
              <td>{fmtDate(b.business_date)}</td>
              <td>{b.employee_name}</td>
              <td>
                {b.visit_slip_number ? `伝票#${b.visit_slip_number} ` : ""}
                {b.line_name ?? (b.kind === "adjustment" ? "調整" : b.kind)}
                {b.reason && <span className="text-xs ml-1" style={{ color: "var(--color-muted)" }}>({b.reason})</span>}
              </td>
              <td style={{ color: Number(b.amount) < 0 ? "var(--color-danger)" : undefined }}>{yen(b.amount)}</td>
              <td>
                <span className={`badge ${b.status === "confirmed" ? "badge-ok" : b.status === "pending" ? "badge-warn" : "badge-danger"}`}>{b.status_label}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AllShiftsTab({ setSave }: { setSave: (s: SaveState) => void }) {
  const { can } = useSession();
  const [range, setRange, bd] = useDateRange();
  const from = range?.from ?? "";
  const to = range?.to ?? "";
  const q = useLoad<ShiftsRes>(range ? `/api/staff/shifts?from=${from}&to=${to}` : null);
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const [correct, setCorrect] = useState<Shift | null>(null);
  const [manual, setManual] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="space-y-3">
      <div className="card">
        <h2 className="font-bold mb-2">出勤・退勤（従業員を選んで操作）</h2>
        <div className="flex flex-wrap gap-2">
          {employees.data?.map((e) => {
            const open = q.data?.open_shifts.find((s) => s.employee === e.id) ?? null;
            return (
              <div key={e.id} className="card-2 flex items-center gap-2">
                <span className="font-semibold">{e.display_name}</span>
                {open && <span className="badge badge-ok">{fmtTime(open.clock_in)}〜</span>}
                <ClockButtons shift={open} employeeId={e.id} onDone={() => q.reload()} setSave={setSave} />
              </div>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <DateRangeBar range={range} onChange={setRange} businessDate={bd} />
        <button className="btn btn-sm" onClick={() => setManual(true)}>
          ＋ 手動登録
        </button>
        {can("export.attendance") && (
          <button className="btn btn-sm" onClick={() => downloadFile(`/api/exports/attendance.csv?from=${from}&to=${to}`).catch(setErr)}>
            勤怠CSV
          </button>
        )}
      </div>
      <ErrorBox error={q.error || err} />
      <div className="card">{q.loading ? <Spinner /> : <ShiftTable shifts={q.data?.shifts ?? []} onCorrect={setCorrect} />}</div>
      {correct && (
        <CorrectModal
          shift={correct}
          onClose={() => setCorrect(null)}
          onSaved={async () => {
            setCorrect(null);
            await q.reload();
          }}
          setSave={setSave}
        />
      )}
      {manual && (
        <ManualModal
          employees={employees.data ?? []}
          onClose={() => setManual(false)}
          onSaved={async () => {
            setManual(false);
            await q.reload();
          }}
          setSave={setSave}
        />
      )}
    </div>
  );
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  // 端末のタイムゾーン（Asia/Tokyo 想定）で表示
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fromLocalInput(v: string): string | null {
  if (!v) return null;
  return new Date(v).toISOString();
}

function CorrectModal({ shift, onClose, onSaved, setSave }: { shift: Shift; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const [clockIn, setClockIn] = useState(toLocalInput(shift.clock_in));
  const [clockOut, setClockOut] = useState(toLocalInput(shift.clock_out));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <ConfirmDialog
      title={`${shift.employee_name} の勤怠を修正`}
      confirmLabel="修正を保存"
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        setBusy(true);
        setSave({ kind: "saving" });
        try {
          await api(`/api/staff/shifts/${shift.id}/correct`, { method: "POST", body: { clock_in: fromLocalInput(clockIn), clock_out: fromLocalInput(clockOut), reason } });
          setSave({ kind: "saved" });
          await onSaved();
        } catch (e) {
          setErr(e);
          setSave({ kind: "error", error: e });
        } finally {
          setBusy(false);
        }
      }}
    >
      <ErrorBox error={err} />
      <div className="space-y-3">
        <Field label="出勤">
          <input type="datetime-local" className="input" value={clockIn} onChange={(e) => setClockIn(e.target.value)} />
        </Field>
        <Field label="退勤（空欄で勤務中）">
          <input type="datetime-local" className="input" value={clockOut} onChange={(e) => setClockOut(e.target.value)} />
        </Field>
        <Field label="修正理由（必須・履歴に残ります）">
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </ConfirmDialog>
  );
}

function ManualModal({ employees, onClose, onSaved, setSave }: { employees: Employee[]; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void }) {
  const [emp, setEmp] = useState<number | null>(null);
  const [clockIn, setClockIn] = useState("");
  const [clockOut, setClockOut] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <ConfirmDialog
      title="勤怠を手動登録"
      confirmLabel="登録"
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        setBusy(true);
        setSave({ kind: "saving" });
        try {
          await api("/api/staff/shifts", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { employee_id: emp, clock_in: fromLocalInput(clockIn), clock_out: fromLocalInput(clockOut), note } });
          setSave({ kind: "saved" });
          await onSaved();
        } catch (e) {
          setErr(e);
          setSave({ kind: "error", error: e });
        } finally {
          setBusy(false);
        }
      }}
    >
      <ErrorBox error={err} />
      <div className="space-y-3">
        <Field label="従業員">
          <select className="input" value={emp ?? ""} onChange={(e) => setEmp(e.target.value ? Number(e.target.value) : null)}>
            <option value="">選択</option>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>
                {e.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="出勤">
          <input type="datetime-local" className="input" value={clockIn} onChange={(e) => setClockIn(e.target.value)} />
        </Field>
        <Field label="退勤（任意）">
          <input type="datetime-local" className="input" value={clockOut} onChange={(e) => setClockOut(e.target.value)} />
        </Field>
        <Field label="メモ">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </ConfirmDialog>
  );
}

function BacksTab() {
  const { can } = useSession();
  const [range, setRange, bd] = useDateRange();
  const from = range?.from ?? "";
  const to = range?.to ?? "";
  const q = useLoad<{ summary: { employee_id: number; employee_name: string; confirmed: string; pending: string; count: number }[]; entries: BackEntry[] }>(range ? `/api/staff/backs?from=${from}&to=${to}` : null);
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangeBar range={range} onChange={setRange} businessDate={bd} />
        {can("export.backs") && (
          <button className="btn btn-sm" onClick={() => downloadFile(`/api/exports/backs.csv?from=${from}&to=${to}`).catch(setErr)}>
            バックCSV
          </button>
        )}
      </div>
      <ErrorBox error={q.error || err} />
      {q.loading && <Spinner />}
      {q.data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {q.data.summary.map((s) => (
              <Stat key={s.employee_id} label={s.employee_name} value={yen(s.confirmed)} sub={`未確定 ${yen(s.pending)} ・ ${s.count}件`} />
            ))}
          </div>
          <div className="card">
            <BackTable backs={q.data.entries} />
          </div>
        </>
      )}
    </div>
  );
}

function PayTab({ setSave }: { setSave: (s: SaveState) => void }) {
  const { withReauth, can } = useSession();
  const [range, setRange, bd] = useDateRange();
  const from = range?.from ?? "";
  const to = range?.to ?? "";
  const [statements, setStatements] = useState<Statement[] | null>(null);
  const [periods, setPeriods] = useState<PayPeriod[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [payFor, setPayFor] = useState<Statement | null>(null);

  async function load() {
    if (!range) return;
    setBusy(true);
    try {
      const s = await withReauth(() => api<Statement[]>(`/api/staff/statements?from=${from}&to=${to}`));
      setStatements(s);
      const p = await api<PayPeriod[]>("/api/staff/pay-periods");
      setPeriods(p);
      setErr(null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  // 期間が決まったら（初期は本日の営業日）集計する
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  return (
    <div className="space-y-3">
      <div className="card-2" style={{ borderColor: "var(--color-accent)" }}>
        <p className="text-lg font-semibold">日払いでその日に精算しているので、ここでの「締め」作業は不要です。</p>
        <p className="text-base" style={{ color: "var(--color-muted)" }}>
          レジ締めとも関係ありません。期間ごとの支払額・未渡しの残りを確認したいときに使ってください。
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <DateRangeBar range={range} onChange={setRange} businessDate={bd} />
        <button className="btn btn-sm btn-primary" onClick={load} disabled={busy}>
          集計する
        </button>
        {can("export.payroll") && (
          <button className="btn btn-sm" onClick={() => withReauth(() => downloadFile(`/api/exports/payroll.csv?from=${from}&to=${to}`)).catch(setErr)}>
            支払CSV
          </button>
        )}
      </div>
      <ErrorBox error={err} />
      {busy && !statements && <Spinner />}
      {statements && (
        <div className="card scroll-x">
          <table className="table num">
            <thead>
              <tr>
                <th>従業員</th>
                <th>勤務時間</th>
                <th>時給分</th>
                <th>確定バック</th>
                <th>支給対象額</th>
                <th>実支払額</th>
                <th>残額</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {statements.map((s) => (
                <tr key={s.employee_id}>
                  <td className="font-semibold">{s.employee_name}</td>
                  <td>
                    {fmtMinutes(s.worked_minutes)}
                    {s.missing_wage_setting && <span className="badge badge-warn ml-1">時給未設定あり</span>}
                  </td>
                  <td>{yen(s.wage_amount)}</td>
                  <td>{yen(s.back_amount)}</td>
                  <td className="font-bold">{yen(s.gross_amount)}</td>
                  <td>{yen(s.paid_amount)}</td>
                  <td className="font-bold" style={{ color: Number(s.balance_amount) < 0 ? "var(--color-danger)" : undefined }}>
                    {yen(s.balance_amount)}
                  </td>
                  <td>
                    {can("payroll.pay") && (
                      <button className="btn btn-sm" onClick={() => setPayFor(s)}>
                        振込等を記録
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
            {statements[0]?.notice ?? "勤務・バックに基づく支払集計です。"} その日のお渡しは「出勤・お渡し」の「渡した」で記録されます（レジの現金出金と関連付き、二重に数えません）。
          </p>
        </div>
      )}
      {periods && (
        <details className="card space-y-2">
          <summary className="cursor-pointer text-lg font-semibold">（任意）月ごとに明細を固定して残す</summary>
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            税理士への提出などで、ある期間の明細を変わらない形で残したいときだけ使います。
          </p>
          <div className="flex justify-end">
        {can("payroll.pay") && (
          <button
            className="btn btn-sm"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setSave({ kind: "saving" });
              try {
                await withReauth(() => api("/api/staff/pay-periods", { method: "POST", body: { start_date: from, end_date: to } }));
                setSave({ kind: "saved", label: "締め期間を作成しました" });
                await load();
              } catch (e) {
                setSave({ kind: "error", error: e });
              } finally {
                setBusy(false);
              }
            }}
          >
            上の期間の明細を固定する
          </button>
        )}
          </div>
          {periods.length === 0 && <Empty>締め期間はありません</Empty>}
          {periods.map((p) => (
            <details key={p.id} className="card-2">
              <summary className="cursor-pointer flex items-center gap-2">
                {fmtDate(p.start_date)} 〜 {fmtDate(p.end_date)} <span className={`badge ${p.status === "closed" ? "badge-accent" : "badge-ok"}`}>{p.status === "closed" ? "締め済" : "未締め"}</span>
                {p.status === "open" && can("payroll.pay") && (
                  <button
                    className="btn btn-sm ml-auto"
                    onClick={async (ev) => {
                      ev.preventDefault();
                      setSave({ kind: "saving", label: "締め処理中…" });
                      try {
                        await withReauth(() => api(`/api/staff/pay-periods/${p.id}/close`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: {} }));
                        setSave({ kind: "saved", label: "締めました（明細を固定）" });
                        await load();
                      } catch (e) {
                        setSave({ kind: "error", error: e });
                      }
                    }}
                  >
                    この期間を締める（明細を固定）
                  </button>
                )}
              </summary>
              {p.statements.length > 0 && (
                <table className="table num mt-2">
                  <thead>
                    <tr>
                      <th>従業員</th>
                      <th>勤務</th>
                      <th>時給分</th>
                      <th>バック</th>
                      <th>支給対象</th>
                      <th>支払済</th>
                      <th>残額</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.statements.map((s) => (
                      <tr key={s.id}>
                        <td>{s.employee_name}</td>
                        <td>{fmtMinutes(s.worked_minutes)}</td>
                        <td>{yen(s.wage_amount)}</td>
                        <td>{yen(s.back_amount)}</td>
                        <td>{yen(s.gross_amount)}</td>
                        <td>{yen(s.paid_amount)}</td>
                        <td>{yen(s.balance_amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </details>
          ))}
        </details>
      )}
      {payFor && (
        <PaymentModal
          statement={payFor}
          onClose={() => setPayFor(null)}
          onSaved={async () => {
            setPayFor(null);
            await load();
          }}
          setSave={setSave}
          withReauth={withReauth}
        />
      )}
    </div>
  );
}

function PaymentModal({ statement, onClose, onSaved, setSave, withReauth }: { statement: Statement; onClose: () => void; onSaved: () => Promise<void>; setSave: (s: SaveState) => void; withReauth: <T>(fn: () => Promise<T>) => Promise<T> }) {
  const [amount, setAmount] = useState(String(Math.max(0, toInt(statement.balance_amount))));
  const [kind, setKind] = useState("settlement");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <Modal
      title={`${statement.employee_name} への支払いを記録（振込など）`}
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
              setSave({ kind: "saving" });
              try {
                await withReauth(() => api("/api/staff/payments", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { employee_id: statement.employee_id, amount: toInt(amount), method: "bank", kind, note } }));
                setSave({ kind: "saved" });
                await onSaved();
              } catch (e) {
                setErr(e);
                setSave({ kind: "error", error: e });
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
      <ErrorBox error={err} />
      <p className="text-sm mb-2" style={{ color: "var(--color-muted)" }}>
        レジ現金からの支払いはここでは登録できません（「開店・日締め・現金」の日払い出金から登録）。
      </p>
      <div className="space-y-3">
        <Field label="種別">
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="settlement">締め支払い（残額）</option>
            <option value="advance">前払い</option>
            <option value="other">その他</option>
          </select>
        </Field>
        <Field label="金額（円）">
          <input className="input input-lg" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} />
        </Field>
        <Field label="メモ">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
