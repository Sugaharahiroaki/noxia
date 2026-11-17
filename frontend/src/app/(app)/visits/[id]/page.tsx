"use client";

import { Tx, useTx } from "@/lib/terms";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { CourseActionDialog } from "@/components/CourseActionDialog";
import { GuestLeaveDialog } from "@/components/GuestLeaveDialog";
import { sortByShift, StaffButton, useShiftGuard } from "@/components/ShiftGuard";
import { TagChip, TagPicker } from "@/components/TagPicker";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, ErrorBox, Modal, Spinner } from "@/components/ui";
import { CANCEL_REASONS, DiscountModal, ReasonModal } from "@/components/VisitDialogs";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime, LINE_TYPE_LABELS, yen } from "@/lib/format";
import { fmtElapsed, fmtRemainShort, undoLines } from "@/lib/pos";
import type { CourseEntry, Employee, ExtendOption, OrderLine, StoreSettings, Table, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { computeAlarm, syncServerClock, useNow } from "@/lib/useServerClock";

type BatchResult = Visit & { created_line_ids: number[] };
type FeeKind = "yobi" | "douhan" | "nomination" | "jounai";
// 並びは設定画面「指名・同伴」と同じ（呼び・同伴・本指名・場内指名）
const FEE_LABELS: Record<FeeKind, string> = { yobi: "呼び", douhan: "同伴", nomination: "本指名", jounai: "場内指名" };
const FEE_FIELD = { yobi: "yobi_fee", douhan: "douhan_fee", nomination: "nomination_fee", jounai: "jounai_fee" } as const;
/** 店舗設定で使わないことにした種類は出さない（POS 設定「指名・同伴」） */
const FEE_ENABLED = { yobi: "yobi_enabled", douhan: "douhan_enabled", nomination: "nomination_enabled", jounai: "jounai_enabled" } as const;
const SET_TYPES = new Set(["course_set", "extension", "course_change"]);

/**
 * 卓詳細。上段: 卓名・タグ・人数の増減 ／ 左: セットの時系列 ／ 右: 延長の候補 ／ 下端: 現在の金額と 注文・会計。
 * 金額は API の quote をそのまま出す。残り時間は終了予定時刻から毎秒計算し直す（再表示・スリープ復帰でも戻る）。
 */
export default function VisitDetailPage() {
  const { id } = useParams<{ id: string }>();
  const visitId = Number(id);
  const router = useRouter();
  const toast = useToast();
  const tx = useTx();
  const { reload: reloadFloor } = useFloor();
  const visitQ = useLoad<Visit>(`/api/visits/${id}`, [], { intervalMs: 30000 });
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const now = useNow();

  const [more, setMore] = useState(false);
  const [unlimited, setUnlimited] = useState<CourseEntry | null>(null);
  const [cancelTarget, setCancelTarget] = useState<OrderLine | null>(null);
  const [fee, setFee] = useState<FeeKind | null>(null);
  const [discount, setDiscount] = useState(false);
  const [move, setMove] = useState(false);
  const [cancelVisit, setCancelVisit] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [extendAsk, setExtendAsk] = useState<{ entry: CourseEntry; option: ExtendOption } | null>(null);
  const [extendBusy, setExtendBusy] = useState(false);
  const [extendError, setExtendError] = useState<string | null>(null);
  const extendIdem = useRef(newIdempotencyKey());

  const visit = visitQ.data;
  useEffect(() => {
    if (visit?.server_now) syncServerClock(visit.server_now);
  }, [visit?.server_now]);
  // 注文画面から戻った直後の「取り消す」など、ほかの画面での変更を反映する
  const reloadVisit = visitQ.reload;
  useEffect(() => {
    const on = () => reloadVisit(true);
    window.addEventListener("noxia:visit-changed", on);
    return () => window.removeEventListener("noxia:visit-changed", on);
  }, [reloadVisit]);

  const casts = useMemo(() => {
    const role = (e: Employee) => (e.this_store_role === "cast" ? 0 : e.this_store_role === "staff" ? 1 : 2);
    return sortByShift(employees.data ?? []).sort((a, b) => role(a) - role(b));
  }, [employees.data]);
  const { guard, element: shiftGuard } = useShiftGuard(() => employees.reload());
  const alarmMode = settings.data?.alarm_mode ?? "both";

  async function quickBatch(items: Record<string, unknown>[], message: string) {
    const r = await api<BatchResult>(`/api/visits/${visitId}/lines/batch`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { items } });
    visitQ.setData(r);
    await reloadFloor();
    toast.show({
      message,
      action: {
        label: "取り消す",
        onClick: async () => {
          try {
            visitQ.setData(await undoLines(visitId, r.created_line_ids));
            await reloadFloor();
            toast.show({ message: "取り消しました", tone: "info" });
          } catch (e) {
            toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
          }
        },
      },
    });
  }

  /** 延長（候補を選んで確認したあと）。確定は 1 回だけ（同じ確認の再送は同じキー） */
  async function doExtend() {
    if (!extendAsk || !visit || extendBusy) return;
    const { entry, option } = extendAsk;
    setExtendBusy(true);
    setExtendError(null);
    try {
      const r = await api<Visit & { created_line_id?: number }>(`/api/visits/${visitId}/course-entries/${entry.id}/extend`, {
        method: "POST",
        idempotencyKey: extendIdem.current,
        body: option.option_id === null ? {} : { option_id: option.option_id },
      });
      visitQ.setData(r);
      setExtendAsk(null);
      extendIdem.current = newIdempotencyKey();
      reloadFloor();
      const lineId = r.created_line_id;
      toast.show({
        message: tx(`${visit.table_name} ${option.minutes}分 延長しました`) + `（${fmtTime(option.to_planned_end_at)}まで ＋${yen(option.amount)}）`,
        action: lineId
          ? {
              label: "取り消す",
              onClick: async () => {
                try {
                  visitQ.setData(await undoLines(visitId, [lineId]));
                  reloadFloor();
                  toast.show({ message: tx("延長を取り消しました"), tone: "info" });
                } catch (e) {
                  toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
                }
              },
            }
          : undefined,
      });
    } catch (e) {
      setExtendError(errorMessage(e));
      if (!(e instanceof ApiError && (e.code === "network" || e.code === "in_progress"))) extendIdem.current = newIdempotencyKey();
    } finally {
      setExtendBusy(false);
    }
  }

  if (visitQ.loading || !visit) return visitQ.error ? <ErrorBox error={visitQ.error} onRetry={() => visitQ.reload(false)} /> : <Spinner label="伝票を読み込み中…" />;

  const isOpen = visit.status === "open";
  const activeLines = visit.lines.filter((l) => l.status === "active");
  const otherLines = activeLines.filter((l) => !SET_TYPES.has(l.line_type));
  const cancelledCount = visit.lines.length - activeLines.length;
  const total = visit.quote?.total ?? "0";
  const activeEntries = visit.course_entries.filter((e) => e.is_active);

  // --- 会計済み・取消済みの伝票 ---
  if (!isOpen) {
    const last = visit.checkouts[visit.checkouts.length - 1];
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center gap-3">
          <button className="btn btn-lg" onClick={() => router.push("/floor")}>
            ← ホール
          </button>
          <h1 className="text-3xl font-bold">
            {visit.table_name} ・ 伝票#{visit.slip_number}
          </h1>
        </div>
        <div className="card space-y-3">
          <p className="text-2xl font-bold">{visit.status === "closed" ? "会計済みです" : "取り消された伝票です"}</p>
          {visit.cancel_reason && <p>取消理由: {visit.cancel_reason}</p>}
          {visit.checkouts.map((c) => (
            <div key={c.id} className="flex justify-between text-xl num">
              <span>
                会計#{c.number}
                {c.status === "voided" && "（取消）"}
              </span>
              <span>{yen(c.total)}</span>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            {last && (
              <Link href={`/checkouts/${last.id}/handwrite`} className="btn btn-xl btn-primary">
                手書き用の金額を見る
              </Link>
            )}
            <button className="btn btn-xl" onClick={() => router.push("/floor")}>
              ホールへ
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 pb-24">
      {/* 上段: 卓名・タグ・人数の増減 */}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-lg" onClick={() => router.push("/floor")}>
          ← ホール
        </button>
        <div className="mr-1">
          <div className="text-3xl font-bold leading-none">{visit.table_name}</div>
          <div className="text-sm mt-0.5" style={{ color: "var(--color-muted)" }}>
            伝票#{visit.slip_number} ・ <span className="num">{fmtElapsed(visit.opened_at, now)}</span> 経過
            {visit.customer_display && ` ・ ${visit.customer_display}`}
          </div>
        </div>
        <button
          className="flex flex-wrap items-center gap-1 rounded-xl px-2 text-left"
          style={{ minHeight: 56, minWidth: 150, maxWidth: 330, background: "var(--color-panel)", border: "2px dashed var(--color-edge)" }}
          onClick={() => setTagOpen(true)}
          aria-label="タグを選ぶ"
        >
          {visit.tags.length === 0 ? <span style={{ color: "var(--color-muted)" }}>＋ <Tx>タグ</Tx></span> : visit.tags.map((t) => <TagChip key={t.id} tag={t} />)}
        </button>
        <div className="ml-auto flex items-center gap-2" role="group" aria-label="人数">
          <button className="btn btn-lg btn-minus" style={{ minWidth: 72 }} onClick={() => setLeaveOpen(true)} aria-label="人を減らす（退店）">
            人−
          </button>
          <div className="text-center leading-tight px-1">
            <div className="text-3xl font-bold num" data-testid="present-count">
              {visit.present_count}
              <span className="text-lg">名</span>
            </div>
            {visit.present_count !== visit.guest_count && (
              <div className="text-xs" style={{ color: "var(--color-muted)" }}>
                来店 {visit.guest_count}名
              </div>
            )}
          </div>
          <button className="btn btn-lg btn-plus" style={{ minWidth: 72 }} onClick={() => router.push(`/visits/${visit.id}/add-course`)} aria-label="人を増やす（途中合流）">
            人＋
          </button>
        </div>
      </div>
      <ErrorBox error={visitQ.error} />

      {/* 左: セットの時系列と注文済み ／ 右: 延長の候補 */}
      <div className="grid grid-cols-12 gap-2">
        <div className="col-span-7 space-y-2">
          <section className="card space-y-2" aria-label="セット">
            {activeEntries.map((e) => {
              const a = computeAlarm(e.planned_end_at, e.is_unlimited, e.is_active, now, alarmMode);
              const cls = a.level === "overdue" ? "alarm-overdue" : a.level === "warn5" ? "alarm-warn5" : a.level === "warn10" ? "alarm-warn10" : "";
              const rows = setRows(e, activeLines);
              return (
                <div key={e.id} className={`card-2 ${cls}`} style={{ padding: "0.6rem" }}>
                  <div className="flex items-baseline justify-between gap-2 mb-1">
                    <span className="text-lg font-bold">
                      {e.label ? `${e.label} ` : ""}
                      {e.course_name}
                      <span className="text-sm font-normal ml-1">在席 {e.present_count}名</span>
                    </span>
                    {e.is_unlimited ? (
                      <span className="text-lg" style={{ color: "var(--color-muted)" }}>
                        無制限
                      </span>
                    ) : (
                      <span className="text-2xl font-bold num" style={{ color: a.remaining <= 0 ? "var(--color-danger)" : undefined }}>
                        {fmtRemainShort(a.remaining)}
                        {a.remaining <= 0 && " ⚠"}
                      </span>
                    )}
                  </div>
                  <div className="space-y-1">
                    {rows.map((r, i) => (
                      <div key={r.line.id} className="flex items-center gap-2 rounded-lg px-2" style={{ minHeight: 46, background: "var(--color-panel)", border: "1px solid var(--color-border)" }}>
                        <span className="font-bold whitespace-nowrap" style={{ minWidth: 76 }}>
                          {i + 1}
                          <Tx>セット</Tx>目
                        </span>
                        <span className="num whitespace-nowrap">{r.from ? `${fmtTime(r.from)}〜${r.to ? fmtTime(r.to) : "無制限"}` : "—"}</span>
                        <span className="text-sm whitespace-nowrap" style={{ color: "var(--color-muted)" }}>
                          {r.minutes ? `${r.minutes}分` : r.line.line_type === "course_change" ? "変更" : ""}
                        </span>
                        <span className="num ml-auto whitespace-nowrap">
                          {yen(r.line.unit_price)} × {Number(r.line.quantity)}
                        </span>
                        <span className="font-bold num whitespace-nowrap" style={{ minWidth: 76, textAlign: "right" }}>
                          {yen(r.line.amount)}
                        </span>
                        <button className="btn btn-sm" style={{ padding: "0 0.6rem" }} disabled={r.line.checkout !== null} onClick={() => setCancelTarget(r.line)} aria-label={`${i + 1}セット目を取り消す`}>
                          取消
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </section>

          <section className="card" aria-label="注文済み">
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-lg font-bold">
                <Tx>指名・同伴・注文</Tx> <span className="num">{otherLines.length}</span>件
              </h2>
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                タップで取り消し{cancelledCount > 0 && ` ・ 取消済 ${cancelledCount}件`}
              </span>
            </div>
            {otherLines.length === 0 && <p style={{ color: "var(--color-muted)" }}>まだありません。下の「注文」から入れます。</p>}
            <div className="space-y-1">
              {otherLines.map((l) => (
                <button
                  key={l.id}
                  className="w-full flex items-center gap-2 rounded-lg px-2 text-left active:scale-[0.99]"
                  style={{ minHeight: 46, background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}
                  onClick={() => l.checkout === null && setCancelTarget(l)}
                  disabled={l.checkout !== null}
                >
                  <span className="text-xs badge"><Tx>{LINE_TYPE_LABELS[l.line_type] ?? l.line_type}</Tx></span>
                  <span className="flex-1">
                    {l.name}
                    {Number(l.quantity) !== 1 && <span className="num"> ×{Number(l.quantity)}</span>}
                    {l.line_type === "product" && l.back_targets.length > 0 && (
                      <span className="text-sm ml-1" style={{ color: "var(--color-muted)" }}>
                        （{l.back_targets.map((t) => t.employee_name).join("・")}）
                      </span>
                    )}
                    {l.checkout !== null && <span className="badge badge-accent ml-1">会計済</span>}
                  </span>
                  <span className="font-bold num">{yen(l.amount)}</span>
                </button>
              ))}
            </div>
          </section>
        </div>

        <div className="col-span-5 space-y-2">
          <section className="card space-y-2" aria-label="延長">
            <h2 className="text-lg font-bold"><Tx>延長（押すまで料金は付きません）</Tx></h2>
            {activeEntries.map((e) => (
              <ExtendPanel
                key={e.id}
                visitId={visitId}
                entry={e}
                showName={activeEntries.length > 1}
                refreshKey={`${e.planned_end_at}:${e.present_count}`}
                onPick={(option) => {
                  extendIdem.current = newIdempotencyKey();
                  setExtendError(null);
                  setExtendAsk({ entry: e, option });
                }}
                onUnlimited={() => setUnlimited(e)}
              />
            ))}
          </section>
          <button className="btn w-full" onClick={() => setMore(true)}>
            <Tx>指名・同伴・値引き・席移動など</Tx>
          </button>
          {settings.data && <PricingInfo st={settings.data} />}
        </div>
      </div>

      {/* --- 無制限へ変更 --- */}
      {unlimited && (
        <CourseActionDialog
          visitId={visitId}
          entryId={unlimited.id}
          kind="unlimited"
          title={`${visit.table_name} 時間無制限へ変更`}
          onClose={() => setUnlimited(null)}
          onDone={async (r) => {
            setUnlimited(null);
            visitQ.setData(r.visit);
            await reloadFloor();
            toast.show({ message: `${visit.table_name} 無制限に変更しました（＋${yen(r.amount)}）` });
          }}
        />
      )}

      {/* --- 明細の取り消し --- */}
      {cancelTarget && (
        <ReasonModal
          title={`「${cancelTarget.name}${Number(cancelTarget.quantity) !== 1 ? ` ×${Number(cancelTarget.quantity)}` : ""}」${yen(cancelTarget.amount)} を取り消す`}
          reasons={CANCEL_REASONS}
          confirmLabel="取り消す"
          onClose={() => setCancelTarget(null)}
          onSubmit={async (reason) => {
            const v = await api<Visit>(`/api/visits/${visitId}/lines/cancel-batch`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { line_ids: [cancelTarget.id], reason } });
            visitQ.setData(v);
            setCancelTarget(null);
            reloadFloor();
            toast.show({ message: `「${cancelTarget.name}」を取り消しました`, tone: "info" });
          }}
        />
      )}

      {/* --- その他の操作 --- */}
      {more && (
        <Modal title="その他の操作" onClose={() => setMore(false)}>
          <div className="grid grid-cols-2 gap-3">
            {(Object.keys(FEE_LABELS) as FeeKind[]).filter((k) => settings.data?.[FEE_ENABLED[k]] !== false).map((k) => (
              <button key={k} className="btn btn-xl" onClick={() => { setMore(false); setFee(k); }}>
                ＋ <Tx>{FEE_LABELS[k]}</Tx>
              </button>
            ))}
            <button className="btn btn-xl" onClick={() => { setMore(false); setDiscount(true); }}>
              − 値引き
            </button>
            <button className="btn btn-xl" onClick={() => { setMore(false); setMove(true); }}>
              席移動
            </button>
            <button className="btn btn-xl btn-danger" onClick={() => { setMore(false); setCancelVisit(true); }}>
              伝票を取消
            </button>
          </div>
        </Modal>
      )}

      {fee && (
        <FeeModal
          kind={fee}
          casts={casts}
          guard={guard}
          fee={settings.data?.[FEE_FIELD[fee]] ?? null}
          onClose={() => setFee(null)}
          onSubmit={async (employeeId, name) => {
            await quickBatch([{ kind: fee, employee_id: employeeId, quantity: 1 }], `${visit.table_name} ${tx(FEE_LABELS[fee])}（${name}）を追加しました`);
            setFee(null);
          }}
        />
      )}

      {discount && (
        <DiscountModal
          onClose={() => setDiscount(false)}
          onSubmit={async (amount, reason) => {
            await quickBatch([{ kind: "discount", amount, reason }], `${visit.table_name} ${yen(amount)} 値引きしました（${reason}）`);
            setDiscount(false);
          }}
        />
      )}

      {move && (
        <MoveModal
          current={visit.table}
          onClose={() => setMove(false)}
          onSubmit={async (t) => {
            await api(`/api/visits/${visitId}/move-table`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { table_id: t.id } });
            await reloadFloor();
            toast.show({ message: `${visit.table_name} → ${t.name} に移動しました（伝票・時間はそのまま）` });
            setMove(false);
            await visitQ.reload();
          }}
        />
      )}

      {cancelVisit && (
        <ReasonModal
          title={`${visit.table_name} の伝票#${visit.slip_number} を取り消す`}
          note="明細はすべて取り消され、在庫・バックも戻ります。履歴は残ります。"
          reasons={["入力ミス（入店していない）", "お客様が入店せず帰った"]}
          confirmLabel="伝票を取り消す"
          danger
          onClose={() => setCancelVisit(false)}
          onSubmit={async (reason) => {
            await api(`/api/visits/${visitId}/cancel`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { reason } });
            await reloadFloor();
            toast.show({ message: `${visit.table_name} の伝票を取り消しました`, tone: "info" });
            router.replace("/floor");
          }}
        />
      )}

      {tagOpen && (
        <TagPicker
          visitId={visitId}
          current={visit.tags}
          onClose={() => setTagOpen(false)}
          onSaved={(v) => {
            visitQ.setData(v);
            setTagOpen(false);
            reloadFloor();
          }}
        />
      )}

      {leaveOpen && (
        <GuestLeaveDialog
          visit={visit}
          onClose={() => setLeaveOpen(false)}
          onDone={(v, seq) => {
            visitQ.setData(v);
            setLeaveOpen(false);
            reloadFloor();
            toast.show({ message: `${visit.table_name} お客様${seq} を退店にしました（在席 ${v.present_count}名）`, tone: "info" });
          }}
        />
      )}

      {extendAsk && (
        <ConfirmDialog title={tx(`${visit.table_name} ${extendAsk.option.minutes}分 延長しますか？`)} confirmLabel={tx("延長する")} busy={extendBusy} onCancel={() => setExtendAsk(null)} onConfirm={doExtend}>
          <div className="space-y-2 text-center">
            <div className="text-xl num">
              終了 {fmtTime(extendAsk.option.from_planned_end_at)} → <strong className="text-4xl">{fmtTime(extendAsk.option.to_planned_end_at)}</strong>
            </div>
            <div className="text-5xl font-bold num" style={{ color: "var(--color-orange)" }}>
              ＋{yen(extendAsk.option.amount)}
            </div>
            <div style={{ color: "var(--color-muted)" }}>
              {yen(extendAsk.option.unit_price)} × 在席 {extendAsk.option.guest_count}名（元の終了予定時刻から加算）
            </div>
            {extendError && (
              <p className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {extendError}
              </p>
            )}
          </div>
        </ConfirmDialog>
      )}

      {/* 下端: 現在の金額と操作 */}
      <div className="fixed left-0 right-0 bottom-0 z-30 px-3 py-2 flex items-center gap-3" style={{ background: "var(--color-panel)", borderTop: "2px solid var(--color-border)" }}>
        {visit.quote?.by_method?.cash && visit.quote.by_method.card ? (
          // 支払方法でサービス料率が変わる店: 現金とカードの金額を並べる（現金＋カードはカードの金額）
          <div className="flex items-end gap-5">
            {(["cash", "card"] as const).map((m) => (
              <div key={m}>
                <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {m === "cash" ? "現金" : "カード"}なら（税込・サービス料 {Number(visit.quote?.by_method?.[m]?.rate ?? 0)}%）
                </div>
                <div className="text-4xl font-bold num leading-none" data-testid={`visit-total-${m}`}>
                  {yen(visit.quote?.by_method?.[m]?.total)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div>
            <div className="text-sm" style={{ color: "var(--color-muted)" }}>
              現在の金額（税込）
            </div>
            <div className="text-4xl font-bold num leading-none" data-testid="visit-total">
              {yen(total)}
            </div>
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn btn-xl" style={{ minWidth: 120 }} onClick={() => router.push(`/visits/${visit.id}/bill`)}>
            明細
          </button>
          <button className="btn btn-xl btn-primary" style={{ minWidth: 150 }} onClick={() => router.push(`/visits/${visit.id}/order`)}>
            注文
          </button>
          <button className="btn btn-xl btn-ok" style={{ minWidth: 190 }} onClick={() => router.push(`/visits/${visit.id}/checkout`)}>
            会計する
          </button>
        </div>
      </div>

      {/* 出勤していない人を選んだときの確認（上の選択画面より手前に出る） */}
      {shiftGuard}
    </div>
  );
}

const ROUND_LABEL: Record<string, string> = { floor: "切り捨て", ceil: "切り上げ", half_up: "四捨五入" };
const SC_TARGET_LABEL: Record<string, string> = { all: "全明細", course_only: "セット・延長のみ", products_only: "商品のみ" };

/**
 * いまの店舗の料金計算の設定（読み取り専用）。卓ごとの率の変更は出さない（UNIFIED_PLAN 6 章 Q2）。
 * 支払方法別の料率（S1）があれば並べて出す。変更は設定画面（権限と再認証が必要）で行う。
 */
function PricingInfo({ st }: { st: StoreSettings }) {
  const byMethod = (
    [
      ["現金", st.service_charge_rate_cash],
      ["カード", st.service_charge_rate_card],
      ["ツケ", st.service_charge_rate_receivable],
    ] as const
  ).filter(([, r]) => r !== null && r !== undefined && Number(r) !== Number(st.service_charge_rate));
  return (
    <section className="card text-sm" style={{ padding: "0.6rem 0.8rem" }} aria-label="料金計算の設定">
      <h2 className="font-bold mb-1">料金計算（店舗の設定・この画面では変更できません）</h2>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5">
        <dt style={{ color: "var(--color-muted)" }}>サービス料</dt>
        <dd className="num">
          {st.service_charge_enabled
            ? `${byMethod.length ? "標準 " : ""}${Number(st.service_charge_rate)}%${byMethod.map(([label, r]) => `・${label} ${Number(r)}%`).join("")}（${SC_TARGET_LABEL[st.service_charge_target] ?? st.service_charge_target}）`
            : "なし"}
        </dd>
        <dt style={{ color: "var(--color-muted)" }}>消費税</dt>
        <dd className="num">
          {Number(st.default_tax_rate)}%（{st.prices_include_tax ? "内税" : "外税"}・{ROUND_LABEL[st.tax_rounding] ?? st.tax_rounding}）
        </dd>
        <dt style={{ color: "var(--color-muted)" }}>合計の丸め</dt>
        <dd className="num">{st.total_rounding_unit > 1 ? `${st.total_rounding_unit}円単位で${ROUND_LABEL[st.total_rounding_mode] ?? st.total_rounding_mode}` : "なし（1円単位）"}</dd>
      </dl>
    </section>
  );
}

type SetRow = { line: OrderLine; from: string | null; to: string | null; minutes: number | null };

/** セットの時系列（1 セット目 = 最初のセット、2 セット目以降 = 延長・変更）。時間帯は終了予定から延長ぶんをさかのぼって並べる */
function setRows(e: CourseEntry, activeLines: OrderLine[]): SetRow[] {
  const mine = activeLines.filter((l) => l.course_entry === e.id).sort((a, b) => a.id - b.id);
  const base = mine.find((l) => l.line_type === "course_set");
  const exts = mine.filter((l) => l.line_type === "extension");
  const changes = mine.filter((l) => l.line_type === "course_change");
  const extMin = (l: OrderLine) => l.extension_minutes ?? e.extension_minutes ?? 0;
  const rows: SetRow[] = [];
  const iso = (ms: number) => new Date(ms).toISOString();
  if (e.planned_end_at) {
    const total = exts.reduce((s, l) => s + extMin(l), 0);
    let cursor = new Date(e.planned_end_at).getTime() - total * 60000;
    if (base) rows.push({ line: base, from: e.started_at, to: iso(cursor), minutes: Math.round((cursor - new Date(e.started_at).getTime()) / 60000) });
    for (const l of exts) {
      const next = cursor + extMin(l) * 60000;
      rows.push({ line: l, from: iso(cursor), to: iso(next), minutes: extMin(l) });
      cursor = next;
    }
  } else {
    if (base) rows.push({ line: base, from: e.started_at, to: null, minutes: null });
    for (const l of exts) rows.push({ line: l, from: null, to: null, minutes: extMin(l) });
  }
  for (const l of changes) rows.push({ line: l, from: null, to: null, minutes: null });
  return rows;
}

/** 延長の候補ごとの見込み金額（サーバー計算）と「延長」ボタン。「自動」「出戻り」は出さない */
function ExtendPanel({ visitId, entry, showName, refreshKey, onPick, onUnlimited }: { visitId: number; entry: CourseEntry; showName: boolean; refreshKey: string; onPick: (o: ExtendOption) => void; onUnlimited: () => void }) {
  const q = useLoad<{ options: ExtendOption[] }>(`/api/visits/${visitId}/course-entries/${entry.id}/extend-options`, [refreshKey], { refetchOnFocus: false });
  if (entry.is_unlimited) {
    return (
      <div className="card-2" style={{ color: "var(--color-muted)" }}>
        {showName && <span className="font-bold mr-2">{entry.label || entry.course_name}</span>}
        <Tx>時間無制限のため延長はありません。</Tx>
      </div>
    );
  }
  if (entry.present_count === 0) {
    return (
      <div className="card-2" style={{ color: "var(--color-muted)" }}>
        {showName && <span className="font-bold mr-2">{entry.label || entry.course_name}</span>}
        <Tx>在席のお客様がいないため、延長はありません。</Tx>
      </div>
    );
  }
  return (
    <div className="space-y-1" role="group" aria-label={`${entry.label || entry.course_name} の延長`}>
      {showName && <div className="font-bold">{entry.label || entry.course_name}</div>}
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner label="延長の料金を確認中…" />}
      {q.data?.options.map((o) => (
        <div key={o.option_id ?? "legacy"} className="flex items-center gap-2 rounded-lg px-2" style={{ minHeight: 60, background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}>
          <div className="leading-tight">
            <div className="text-xl font-bold num">{o.minutes}分</div>
            <div className="text-xs num" style={{ color: "var(--color-muted)" }}>
              〜{fmtTime(o.to_planned_end_at)}
            </div>
          </div>
          <div className="ml-auto text-right leading-tight">
            <div className="text-xs" style={{ color: "var(--color-muted)" }}>
              <Tx>延長した場合</Tx> ＋
            </div>
            <div className="text-xl font-bold num" style={{ color: "var(--color-orange)" }}>
              {yen(o.amount)}
            </div>
          </div>
          <button className="btn btn-primary" style={{ minWidth: 84 }} onClick={() => onPick(o)} aria-label={`${o.minutes}分 延長`}>
            <Tx>延長</Tx>
          </button>
        </div>
      ))}
      <button className="btn btn-sm w-full" onClick={onUnlimited}>
        無制限へ変更
      </button>
    </div>
  );
}

function FeeModal({ kind, casts, fee, guard, onClose, onSubmit }: { kind: FeeKind; casts: Employee[]; fee: string | null; guard: (e: Employee) => Promise<boolean>; onClose: () => void; onSubmit: (employeeId: number, name: string) => Promise<void> }) {
  const [emp, setEmp] = useState<Employee | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tx = useTx();
  const label = tx(FEE_LABELS[kind]);
  return (
    <Modal
      title={`${label}を追加`}
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-xl btn-primary"
            disabled={!emp || busy || fee === null}
            onClick={async () => {
              if (!emp) return;
              setBusy(true);
              setError(null);
              try {
                await onSubmit(emp.id, emp.display_name);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "処理中…" : emp ? `${emp.display_name}の${label}を追加（${yen(fee)}）` : `誰の${label}か選んでください`}
          </button>
        </>
      }
    >
      {fee === null ? (
        <p className="text-lg font-bold" style={{ color: "var(--color-danger)" }}>
          {label}料が店舗設定で未設定です。設定してから追加してください（0円では登録しません）。
        </p>
      ) : (
        <div className="grid grid-cols-3 md:grid-cols-4 gap-3">
          {casts.map((e) => (
            <StaffButton
              key={e.id}
              e={e}
              selected={emp?.id === e.id}
              onClick={async () => {
                if (emp?.id !== e.id && !(await guard(e))) return;
                setEmp(e);
              }}
            />
          ))}
        </div>
      )}
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}

function MoveModal({ current, onClose, onSubmit }: { current: number; onClose: () => void; onSubmit: (t: Table) => Promise<void> }) {
  const tables = useLoad<Table[]>("/api/store/tables?active=1");
  const { state } = useFloor();
  const occupied = new Set((state?.tables ?? []).filter((t) => t.visits.length).map((t) => t.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal title="どの卓へ移動しますか？（伝票・時間はそのまま）" onClose={busy ? undefined : onClose} wide>
      <div className="grid grid-cols-3 md:grid-cols-4 gap-3">
        {tables.data?.map((t) => (
          <button
            key={t.id}
            className={`btn btn-xl flex-col h-auto py-4 ${!occupied.has(t.id) ? "btn-primary" : ""}`}
            disabled={t.id === current || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(t);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <span className="text-2xl">{t.name}</span>
            <span className="text-sm font-normal">{t.id === current ? "いまの卓" : occupied.has(t.id) ? "使用中" : "空席"}</span>
          </button>
        ))}
      </div>
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}
