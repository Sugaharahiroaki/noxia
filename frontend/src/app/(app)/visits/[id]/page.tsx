"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { CourseActionDialog, type CourseActionKind } from "@/components/CourseActionDialog";
import { StaffButton, useShiftGuard } from "@/components/ShiftGuard";
import { useToast } from "@/components/Toast";
import { ErrorBox, Modal, NumberPad, Spinner } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime, LINE_TYPE_LABELS, toInt, yen } from "@/lib/format";
import { fmtElapsed, fmtRemainShort, undoLines } from "@/lib/pos";
import { useSession } from "@/lib/session";
import type { Category, CourseEntry, Employee, OrderLine, Product, StoreSettings, Table, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { computeAlarm, syncServerClock, useNow } from "@/lib/useServerClock";

type CartItem = { key: string; productId: number; qty: number; cast: number | null; keep: boolean };
type BatchResult = Visit & { created_line_ids: number[] };

/** バック対象を聞く商品（キャストドリンク・ボトル） */
const ASK_CAST_KINDS = new Set(["cast_drink", "bottle"]);
const CANCEL_REASONS = ["入力ミス", "お客様のキャンセル", "提供できなかった"];
const DISCOUNT_REASONS = ["常連割引", "サービス", "端数調整"];

export default function VisitPosPage() {
  const { id } = useParams<{ id: string }>();
  const visitId = Number(id);
  const router = useRouter();
  const toast = useToast();
  const { store } = useSession();
  const { reload: reloadFloor } = useFloor();
  const visitQ = useLoad<Visit>(`/api/visits/${id}`, [], { intervalMs: 30000 });
  const products = useLoad<Product[]>("/api/store/products?active=1");
  const categories = useLoad<Category[]>("/api/store/categories?active=1");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const now = useNow();

  const cartKey = `yorumiru.cart.${store?.id}.${visitId}`;
  const [cart, setCart] = useState<CartItem[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(cartKey) ?? "[]") as CartItem[];
    } catch {
      return [];
    }
  });
  const [cat, setCat] = useState<number | null>(null);
  const [castPick, setCastPick] = useState<{ productId: number } | { cartKey: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cartError, setCartError] = useState<string | null>(null);
  const idem = useRef(newIdempotencyKey());
  const [leaveAsk, setLeaveAsk] = useState(false);
  const [more, setMore] = useState(false);
  const [courseAction, setCourseAction] = useState<{ entry: CourseEntry; kind: CourseActionKind } | null>(null);
  const [cancelTarget, setCancelTarget] = useState<OrderLine | null>(null);
  const [fee, setFee] = useState<"nomination" | "douhan" | null>(null);
  const [discount, setDiscount] = useState(false);
  const [move, setMove] = useState(false);
  const [cancelVisit, setCancelVisit] = useState(false);

  // カートは端末内に一時保存（誤って画面を離れても消えない）
  useEffect(() => {
    try {
      if (cart.length) sessionStorage.setItem(cartKey, JSON.stringify(cart));
      else sessionStorage.removeItem(cartKey);
    } catch {
      /* ignore */
    }
  }, [cart, cartKey]);

  const visit = visitQ.data;
  useEffect(() => {
    if (visit?.server_now) syncServerClock(visit.server_now);
  }, [visit?.server_now]);

  const productMap = useMemo(() => new Map((products.data ?? []).map((p) => [p.id, p])), [products.data]);
  const empMap = useMemo(() => new Map((employees.data ?? []).map((e) => [e.id, e])), [employees.data]);
  // 出勤中の人を先に、その中ではキャスト→スタッフ→その他の順
  const casts = useMemo(() => {
    const role = (e: Employee) => (e.this_store_role === "cast" ? 0 : e.this_store_role === "staff" ? 1 : 2);
    const shift = (e: Employee) => (e.shift_status === "working" ? 0 : e.shift_status === "on_break" ? 1 : e.shift_status === "off" ? 2 : 3);
    return [...(employees.data ?? [])].sort((a, b) => shift(a) - shift(b) || role(a) - role(b));
  }, [employees.data]);
  const { guard, element: shiftGuard } = useShiftGuard(() => employees.reload());
  const shownProducts = useMemo(() => (products.data ?? []).filter((p) => (cat ? p.category === cat : true)), [products.data, cat]);
  const cartLines = cart.map((c) => ({ ...c, product: productMap.get(c.productId) })).filter((c) => c.product);
  const cartTotal = cartLines.reduce((s, c) => s + Number(c.product!.price) * c.qty, 0);
  const cartCount = cartLines.reduce((s, c) => s + c.qty, 0);
  const alarmMode = settings.data?.alarm_mode ?? "both";

  function changeCart(fn: (c: CartItem[]) => CartItem[]) {
    setCart(fn);
    setCartError(null);
    idem.current = newIdempotencyKey(); // 内容が変わったら別の注文として扱う
  }

  function addToCart(productId: number, cast: number | null) {
    changeCart((c) => {
      const same = c.find((x) => x.productId === productId && x.cast === cast && !x.keep);
      if (same) return c.map((x) => (x === same ? { ...x, qty: x.qty + 1 } : x));
      return [...c, { key: `${productId}-${cast}-${Date.now()}`, productId, qty: 1, cast, keep: false }];
    });
  }

  function tapProduct(p: Product) {
    if (ASK_CAST_KINDS.has(p.category_kind)) setCastPick({ productId: p.id });
    else addToCart(p.id, null);
  }

  async function submitCart(thenLeave = true) {
    if (!cartLines.length || !visit) return;
    setSubmitting(true);
    setCartError(null);
    try {
      const items = cartLines.map((c) => ({
        kind: "product",
        product_id: c.productId,
        quantity: c.qty,
        back_targets: c.cast ? [{ employee_id: c.cast, share_type: "percent", share_value: 100 }] : [],
        register_bottle_keep: c.keep,
      }));
      const r = await api<BatchResult>(`/api/visits/${visitId}/lines/batch`, { method: "POST", idempotencyKey: idem.current, body: { items } });
      const ids = r.created_line_ids;
      const msg = `${visit.table_name}に ${cartCount}点 注文しました（${yen(cartTotal)}）`;
      setCart([]);
      idem.current = newIdempotencyKey();
      await reloadFloor();
      toast.show({
        message: msg,
        action: {
          label: "取り消す",
          onClick: async () => {
            try {
              await undoLines(visitId, ids);
              await reloadFloor();
              toast.show({ message: "いまの注文を取り消しました", tone: "info" });
            } catch (e) {
              toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
            }
          },
        },
      });
      if (thenLeave) router.replace("/floor");
      else visitQ.setData(r);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) {
        setCartError(`${e.message} もう一度「注文確定」を押しても二重には登録されません。`);
      } else {
        setCartError(errorMessage(e));
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function quickBatch(items: Record<string, unknown>[], message: string, leave: boolean) {
    const r = await api<BatchResult>(`/api/visits/${visitId}/lines/batch`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { items } });
    await reloadFloor();
    toast.show({
      message,
      action: {
        label: "取り消す",
        onClick: async () => {
          try {
            const v = await undoLines(visitId, r.created_line_ids);
            await reloadFloor();
            if (!leave) visitQ.setData(v);
            toast.show({ message: "取り消しました", tone: "info" });
          } catch (e) {
            toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
          }
        },
      },
    });
    if (leave) router.replace("/floor");
    else visitQ.setData(r);
  }

  function goFloor() {
    if (cartLines.length) setLeaveAsk(true);
    else router.push("/floor");
  }

  if (visitQ.loading || !visit) return visitQ.error ? <ErrorBox error={visitQ.error} onRetry={() => visitQ.reload(false)} /> : <Spinner label="伝票を読み込み中…" />;

  const isOpen = visit.status === "open";
  const activeLines = visit.lines.filter((l) => l.status === "active");
  const cancelledCount = visit.lines.length - activeLines.length;
  const total = visit.quote?.total ?? "0";

  // --- 会計済み・取消済みの伝票 ---
  if (!isOpen) {
    const last = visit.checkouts[visit.checkouts.length - 1];
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center gap-3">
          <button className="btn btn-lg" onClick={() => router.push("/floor")}>
            ← 卓一覧
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
              <span>会計#{c.number}{c.status === "voided" && "（取消）"}</span>
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
              卓一覧へ
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* 上部バー */}
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-xl" onClick={goFloor}>
          ← 卓一覧
        </button>
        <div>
          <div className="text-4xl font-bold leading-none">{visit.table_name}</div>
          <div className="text-base mt-1" style={{ color: "var(--color-muted)" }}>
            伝票#{visit.slip_number} ・ {visit.guest_count}名 ・ <span className="num">{fmtElapsed(visit.opened_at, now)}</span> 経過
            {visit.customer_display && ` ・ ${visit.customer_display}`}
          </div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-sm" style={{ color: "var(--color-muted)" }}>
            いまの合計（税込）
          </div>
          <div className="text-4xl font-bold num">{yen(total)}</div>
        </div>
        <button
          className="btn btn-xl btn-ok"
          onClick={() => (cartLines.length ? setLeaveAsk(true) : router.push(`/visits/${visit.id}/checkout`))}
          style={{ minWidth: 180 }}
        >
          会計する
        </button>
      </div>
      <ErrorBox error={visitQ.error} />

      <div className="grid grid-cols-12 gap-3">
        {/* 左: 時間・注文済み */}
        <div className="col-span-12 lg:col-span-5 space-y-3">
          <section className="card space-y-2">
            {visit.course_entries
              .filter((e) => e.is_active)
              .map((e) => {
                const a = computeAlarm(e.planned_end_at, e.is_unlimited, e.is_active, now, alarmMode);
                const cls = a.level === "overdue" ? "alarm-overdue" : a.level === "warn5" ? "alarm-warn5" : a.level === "warn10" ? "alarm-warn10" : "";
                return (
                  <div key={e.id} className={`card-2 ${cls}`}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-xl font-bold">
                        {e.label ? `${e.label} ` : ""}
                        {e.course_name.replace("飲み放題・", "")}
                        <span className="text-base font-normal ml-1">{e.guest_count}名</span>
                      </span>
                      {e.is_unlimited ? (
                        <span className="text-xl" style={{ color: "var(--color-muted)" }}>
                          無制限
                        </span>
                      ) : (
                        <span className="text-2xl font-bold num">{fmtRemainShort(a.remaining)}</span>
                      )}
                    </div>
                    {!e.is_unlimited && (
                      <>
                        <div className="text-base num" style={{ color: "var(--color-muted)" }}>
                          終了予定 {fmtTime(e.planned_end_at)}
                          {e.extension_count > 0 && ` ・ 延長${e.extension_count}回`}
                        </div>
                        <div className="grid grid-cols-2 gap-2 mt-2">
                          <button className="btn btn-lg" onClick={() => setCourseAction({ entry: e, kind: "extend" })}>
                            1時間延長
                          </button>
                          <button className="btn btn-lg" onClick={() => setCourseAction({ entry: e, kind: "unlimited" })}>
                            無制限へ
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
          </section>

          <section className="card">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-xl font-bold">注文済み</h2>
              <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                タップで取り消し{cancelledCount > 0 && ` ・ 取消済 ${cancelledCount}件`}
              </span>
            </div>
            <div className="space-y-1 max-h-[38vh] overflow-y-auto pr-1">
              {activeLines.map((l) => (
                <button
                  key={l.id}
                  className="w-full flex items-center gap-2 rounded-xl px-3 py-3 text-left active:scale-[0.99]"
                  style={{ background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}
                  onClick={() => l.checkout === null && setCancelTarget(l)}
                  disabled={l.checkout !== null}
                >
                  <span className="text-xs badge">{LINE_TYPE_LABELS[l.line_type]}</span>
                  <span className="flex-1 text-lg">
                    {l.name}
                    {Number(l.quantity) !== 1 && <span className="num"> ×{Number(l.quantity)}</span>}
                    {l.back_targets.length > 0 && (
                      <span className="text-sm ml-1" style={{ color: "var(--color-muted)" }}>
                        （{l.back_targets.map((t) => t.employee_name).join("・")}）
                      </span>
                    )}
                    {l.checkout !== null && <span className="badge badge-accent ml-1">会計済</span>}
                  </span>
                  <span className="text-lg font-bold num">{yen(l.amount)}</span>
                </button>
              ))}
            </div>
          </section>

          <button className="btn btn-xl w-full" onClick={() => setMore(true)}>
            指名・同伴・値引き・席移動など
          </button>
        </div>

        {/* 右: 商品とカート */}
        <div className="col-span-12 lg:col-span-7 space-y-3">
          <div className="flex gap-2 scroll-x pb-1">
            <button className={`btn btn-lg whitespace-nowrap ${cat === null ? "btn-primary" : ""}`} onClick={() => setCat(null)}>
              すべて
            </button>
            {categories.data?.map((c) => (
              <button key={c.id} className={`btn btn-lg whitespace-nowrap ${cat === c.id ? "btn-primary" : ""}`} onClick={() => setCat(c.id)}>
                {c.name}
              </button>
            ))}
          </div>
          {products.loading && <Spinner />}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {shownProducts.map((p) => {
              const inCart = cartLines.filter((c) => c.productId === p.id).reduce((s, c) => s + c.qty, 0);
              return (
                <button
                  key={p.id}
                  className="btn flex-col items-start justify-between text-left h-auto py-3 px-4 relative"
                  style={{ minHeight: 96, ...(inCart ? { borderColor: "var(--color-ok)", borderWidth: 2 } : {}) }}
                  onClick={() => tapProduct(p)}
                >
                  <span className="text-xl font-bold leading-tight">{p.name}</span>
                  <span className="text-lg num opacity-80">{yen(p.price)}</span>
                  {inCart > 0 && (
                    <span className="absolute top-2 right-2 rounded-full px-2.5 py-0.5 text-lg font-bold" style={{ background: "var(--color-ok)", color: "#062" }}>
                      {inCart}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* カート */}
          <section className="card space-y-2 sticky bottom-2" style={{ borderColor: cartLines.length ? "var(--color-ok)" : undefined, borderWidth: cartLines.length ? 2 : 1 }}>
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-bold">これから注文 {cartCount > 0 && <span className="num">{cartCount}点</span>}</h2>
              {cartLines.length > 0 && (
                <button className="btn btn-sm btn-ghost" onClick={() => changeCart(() => [])}>
                  全部消す
                </button>
              )}
            </div>
            {cartLines.length === 0 && (
              <p className="text-lg" style={{ color: "var(--color-muted)" }}>
                上の商品をタップすると、ここに入ります。
              </p>
            )}
            <div className="space-y-1 max-h-[28vh] overflow-y-auto pr-1">
              {cartLines.map((c) => (
                <div key={c.key} className="flex items-center gap-2 rounded-xl px-2 py-1.5" style={{ background: "var(--color-panel2)" }}>
                  <span className="flex-1 text-lg font-semibold min-w-0">
                    {c.product!.name}
                    {ASK_CAST_KINDS.has(c.product!.category_kind) || c.cast ? (
                      <button className="badge badge-accent ml-2 text-base" onClick={() => setCastPick({ cartKey: c.key })}>
                        {c.cast ? empMap.get(c.cast)?.display_name ?? "?" : "バックなし"} ✎
                      </button>
                    ) : null}
                    {c.product!.is_bottle_keepable && visit.customer && (
                      <button
                        className={`badge ml-2 text-base ${c.keep ? "badge-ok" : ""}`}
                        onClick={() => changeCart((cs) => cs.map((x) => (x.key === c.key ? { ...x, keep: !x.keep } : x)))}
                      >
                        {c.keep ? "✓ キープする" : "キープ？"}
                      </button>
                    )}
                  </span>
                  <button className="btn" style={{ minHeight: 52, minWidth: 52 }} onClick={() => changeCart((cs) => cs.flatMap((x) => (x.key === c.key ? (x.qty > 1 ? [{ ...x, qty: x.qty - 1 }] : []) : [x])))} aria-label="1つ減らす">
                    −
                  </button>
                  <span className="text-2xl font-bold num w-10 text-center">{c.qty}</span>
                  <button className="btn" style={{ minHeight: 52, minWidth: 52 }} onClick={() => changeCart((cs) => cs.map((x) => (x.key === c.key ? { ...x, qty: x.qty + 1 } : x)))} aria-label="1つ増やす">
                    ＋
                  </button>
                  <span className="text-lg font-bold num w-24 text-right">{yen(Number(c.product!.price) * c.qty)}</span>
                </div>
              ))}
            </div>
            {cartError && (
              <p className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {cartError}
              </p>
            )}
            <button className="btn btn-xl btn-primary w-full" disabled={!cartLines.length || submitting} onClick={() => submitCart(true)}>
              {submitting ? "送信中…" : cartLines.length ? `注文確定（${cartCount}点 ${yen(cartTotal)}）` : "注文確定"}
            </button>
          </section>
        </div>
      </div>

      {/* --- 誰のドリンク？ --- */}
      {castPick && (
        <Modal title={"productId" in castPick ? `${productMap.get(castPick.productId)?.name} ・ 誰の？（バック）` : "バック対象を変更"} onClose={() => setCastPick(null)} wide>
          <div className="grid grid-cols-3 md:grid-cols-4 gap-3">
            {casts.map((e) => (
              <StaffButton
                key={e.id}
                e={e}
                selected={visit.primary_employee === e.id}
                onClick={async () => {
                  if (!(await guard(e))) return;
                  if ("productId" in castPick) addToCart(castPick.productId, e.id);
                  else changeCart((cs) => cs.map((x) => (x.key === castPick.cartKey ? { ...x, cast: e.id } : x)));
                  setCastPick(null);
                }}
              />
            ))}
            <button
              className="btn btn-xl"
              onClick={() => {
                if ("productId" in castPick) addToCart(castPick.productId, null);
                else changeCart((cs) => cs.map((x) => (x.key === castPick.cartKey ? { ...x, cast: null } : x)));
                setCastPick(null);
              }}
            >
              なし
            </button>
          </div>
        </Modal>
      )}

      {/* --- 未確定のまま離れる --- */}
      {leaveAsk && (
        <Modal title={`まだ注文確定していない商品が ${cartCount}点 あります`} onClose={() => setLeaveAsk(false)}>
          <div className="grid gap-3">
            <button
              className="btn btn-xl btn-primary"
              onClick={async () => {
                setLeaveAsk(false);
                await submitCart(true);
              }}
            >
              注文確定してから卓一覧へ
            </button>
            <button
              className="btn btn-xl"
              onClick={() => {
                setLeaveAsk(false);
                changeCart(() => []);
                router.push("/floor");
              }}
            >
              注文しないで戻る（カートを消す）
            </button>
            <button className="btn btn-xl btn-ghost" onClick={() => setLeaveAsk(false)}>
              この画面に残る
            </button>
          </div>
        </Modal>
      )}

      {/* --- 延長・無制限 --- */}
      {courseAction && (
        <CourseActionDialog
          visitId={visitId}
          entryId={courseAction.entry.id}
          kind={courseAction.kind}
          title={`${visit.table_name} ${courseAction.kind === "extend" ? "1時間延長" : "時間無制限へ変更"}`}
          onClose={() => setCourseAction(null)}
          onDone={async (r) => {
            setCourseAction(null);
            await reloadFloor();
            const lineId = r.visit.created_line_id;
            toast.show({
              message: r.kind === "extend" ? `${visit.table_name} 延長しました（${fmtTime(r.toEnd)}まで ＋${yen(r.amount)}）` : `${visit.table_name} 無制限に変更しました（＋${yen(r.amount)}）`,
              action:
                r.kind === "extend" && lineId
                  ? {
                      label: "取り消す",
                      onClick: async () => {
                        try {
                          await undoLines(visitId, [lineId]);
                          await reloadFloor();
                          toast.show({ message: "延長を取り消しました", tone: "info" });
                        } catch (e) {
                          toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
                        }
                      },
                    }
                  : undefined,
            });
            router.replace("/floor");
          }}
        />
      )}

      {/* --- 注文の取り消し --- */}
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
            <button className="btn btn-xl" onClick={() => { setMore(false); setFee("nomination"); }}>
              ＋ 指名
            </button>
            <button className="btn btn-xl" onClick={() => { setMore(false); setFee("douhan"); }}>
              ＋ 同伴
            </button>
            <button className="btn btn-xl" onClick={() => { setMore(false); setDiscount(true); }}>
              − 値引き
            </button>
            <button className="btn btn-xl" onClick={() => router.push(`/visits/${visit.id}/add-course`)}>
              ＋ 途中合流
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
          fee={fee === "nomination" ? settings.data?.nomination_fee ?? null : settings.data?.douhan_fee ?? null}
          onClose={() => setFee(null)}
          onSubmit={async (employeeId, name) => {
            const label = fee === "nomination" ? "指名" : "同伴";
            await quickBatch([{ kind: fee, employee_id: employeeId, quantity: 1 }], `${visit.table_name} ${label}（${name}）を追加しました`, true);
            setFee(null);
          }}
        />
      )}

      {discount && (
        <DiscountModal
          onClose={() => setDiscount(false)}
          onSubmit={async (amount, reason) => {
            await quickBatch([{ kind: "discount", amount, reason }], `${visit.table_name} ${yen(amount)} 値引きしました（${reason}）`, false);
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
            router.replace("/floor");
          }}
        />
      )}

      {cancelVisit && (
        <ReasonModal
          title={`${visit.table_name} の伝票#${visit.slip_number} を取り消す`}
          note="明細はすべて取り消され、在庫・バックも戻ります。"
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

      {/* 出勤していない人を選んだときの確認（上の選択画面より手前に出る） */}
      {shiftGuard}
    </div>
  );
}

/** 理由を大きなボタンで選ぶ（その他は入力） */
function ReasonModal({
  title,
  note,
  reasons,
  confirmLabel,
  danger,
  onClose,
  onSubmit,
}: {
  title: string;
  note?: string;
  reasons: string[];
  confirmLabel: string;
  danger?: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reason = picked === "その他" ? other.trim() : picked;
  return (
    <Modal
      title={title}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className={`btn btn-xl ${danger ? "btn-danger" : "btn-primary"}`}
            disabled={!reason || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(reason!);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "処理中…" : confirmLabel}
          </button>
        </>
      }
    >
      {note && <p className="text-lg mb-3">{note}</p>}
      <div className="text-base mb-2" style={{ color: "var(--color-muted)" }}>
        理由（記録されます）
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {[...reasons, "その他"].map((r) => (
          <button key={r} className={`btn btn-lg ${picked === r ? "btn-primary" : ""}`} onClick={() => setPicked(r)} aria-pressed={picked === r}>
            {r}
          </button>
        ))}
      </div>
      {picked === "その他" && <input className="input mt-3" autoFocus value={other} onChange={(e) => setOther(e.target.value)} placeholder="理由を入力" />}
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}

function FeeModal({ kind, casts, fee, guard, onClose, onSubmit }: { kind: "nomination" | "douhan"; casts: Employee[]; fee: string | null; guard: (e: Employee) => Promise<boolean>; onClose: () => void; onSubmit: (employeeId: number, name: string) => Promise<void> }) {
  const [emp, setEmp] = useState<Employee | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = kind === "nomination" ? "指名" : "同伴";
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

function DiscountModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (amount: number, reason: string) => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reason = picked === "その他" ? other.trim() : picked;
  return (
    <Modal
      title="値引き"
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-xl btn-primary"
            disabled={!toInt(amount) || !reason || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(toInt(amount), reason!);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "処理中…" : `${yen(toInt(amount))} 値引きする`}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-5xl font-bold num text-right mb-3">{yen(toInt(amount))}</div>
          <NumberPad value={amount} onChange={setAmount} quick={[500, 1000, 2000, 5000]} />
        </div>
        <div>
          <div className="text-base mb-2" style={{ color: "var(--color-muted)" }}>
            理由（記録されます）
          </div>
          <div className="grid gap-2">
            {[...DISCOUNT_REASONS, "その他"].map((r) => (
              <button key={r} className={`btn btn-lg ${picked === r ? "btn-primary" : ""}`} onClick={() => setPicked(r)}>
                {r}
              </button>
            ))}
          </div>
          {picked === "その他" && <input className="input mt-3" value={other} onChange={(e) => setOther(e.target.value)} placeholder="理由を入力" />}
          {error && (
            <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </p>
          )}
        </div>
      </div>
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
