"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { sortByShift, isOnShift, SHIFT_LABEL, useShiftGuard } from "@/components/ShiftGuard";
import { SplitBackPanel, type SplitShare } from "@/components/SplitBackPanel";
import { useToast } from "@/components/Toast";
import { ErrorBox, Spinner } from "@/components/ui";
import { DiscountModal } from "@/components/VisitDialogs";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { yen } from "@/lib/format";
import { undoLines } from "@/lib/pos";
import { useSession } from "@/lib/session";
import type { Category, Employee, Product, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Target = { kind: "customer" } | { kind: "emp"; id: number } | { kind: "split"; shares: SplitShare[] };
type Row = { key: string; productId: number; qty: number; target: Target; keep: boolean };
type Draft = { rows: Row[]; cat: number | null; productId: number | null };
type BatchResult = Visit & { created_line_ids: number[] };

const sameTarget = (a: Target, b: Target) => a.kind === b.kind && (a.kind !== "emp" || (b.kind === "emp" && a.id === b.id)) && a.kind !== "split";

/**
 * 注文（4 列）: カテゴリー｜メニューと単価｜誰に付けるか｜今回の注文。
 * 確定するまでは画面内の下書き（端末の sessionStorage に卓＝伝票ごとに残す）。確定は lines/batch を 1 回だけ。
 */
export default function OrderPage() {
  const { id } = useParams<{ id: string }>();
  const visitId = Number(id);
  const router = useRouter();
  const toast = useToast();
  const { store } = useSession();
  const { reload: reloadFloor } = useFloor();
  const visitQ = useLoad<Visit>(`/api/visits/${id}`);
  const products = useLoad<Product[]>("/api/store/products?active=1");
  const categories = useLoad<Category[]>("/api/store/categories?active=1");
  const employees = useLoad<Employee[]>("/api/staff/employees?active=1");
  const { guard, element: shiftGuard } = useShiftGuard(() => employees.reload());

  const draftKey = `noxia.order.${store?.id}.${visitId}`;
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const d = JSON.parse(sessionStorage.getItem(draftKey) ?? "null") as Draft | null;
      if (d && Array.isArray(d.rows)) return d;
    } catch {
      /* ignore */
    }
    return { rows: [], cat: null, productId: null };
  });
  const [staffList, setStaffList] = useState(false);
  const [split, setSplit] = useState<{ productId: number; rowKey: string | null } | null>(null);
  const [discount, setDiscount] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idem = useRef(newIdempotencyKey());

  // 下書きは端末内に一時保存（折半のパネルを開閉しても、画面を離れても、元の卓・カテゴリー・商品・入力途中の行が残る）
  useEffect(() => {
    try {
      if (draft.rows.length || draft.cat !== null || draft.productId !== null) sessionStorage.setItem(draftKey, JSON.stringify(draft));
      else sessionStorage.removeItem(draftKey);
    } catch {
      /* ignore */
    }
  }, [draft, draftKey]);

  const visit = visitQ.data;
  const productMap = useMemo(() => new Map((products.data ?? []).map((p) => [p.id, p])), [products.data]);
  const empMap = useMemo(() => new Map((employees.data ?? []).map((e) => [e.id, e])), [employees.data]);
  const casts = useMemo(() => sortByShift((employees.data ?? []).filter((e) => e.this_store_role === "cast")), [employees.data]);
  const staffs = useMemo(() => sortByShift((employees.data ?? []).filter((e) => e.this_store_role !== "cast")), [employees.data]);
  const cats = categories.data ?? [];
  const cat = draft.cat ?? cats[0]?.id ?? null;
  const shownProducts = (products.data ?? []).filter((p) => p.category === cat);
  const selected = draft.productId !== null ? productMap.get(draft.productId) ?? null : null;
  const rows = draft.rows.filter((r) => productMap.has(r.productId));
  const count = rows.reduce((s, r) => s + r.qty, 0);

  function change(fn: (d: Draft) => Draft) {
    setDraft(fn);
    setError(null);
    idem.current = newIdempotencyKey(); // 内容が変わったら別の注文として扱う
  }
  function addRow(productId: number, target: Target) {
    change((d) => {
      const same = d.rows.find((r) => r.productId === productId && !r.keep && sameTarget(r.target, target));
      if (same) return { ...d, rows: d.rows.map((r) => (r === same ? { ...r, qty: r.qty + 1 } : r)) };
      return { ...d, rows: [...d.rows, { key: `${productId}-${Date.now()}-${d.rows.length}`, productId, qty: 1, target, keep: false }] };
    });
  }
  const setQty = (key: string, delta: number) => change((d) => ({ ...d, rows: d.rows.map((r) => (r.key === key ? { ...r, qty: Math.max(0, r.qty + delta) } : r)) }));

  function targetLabel(t: Target): string {
    if (t.kind === "customer") return "お客様";
    if (t.kind === "emp") return empMap.get(t.id)?.display_name ?? "?";
    return `折半 ${t.shares.map((s) => `${empMap.get(s.employee)?.display_name ?? "?"} ${yen(s.amount)}`).join("・")}`;
  }

  async function submit() {
    if (!visit || submitting) return;
    const send = rows.filter((r) => r.qty > 0); // 数量 0 の行は送らない
    if (!send.length) return;
    setSubmitting(true);
    setError(null);
    try {
      const items = send.map((r) => ({
        kind: "product",
        product_id: r.productId,
        quantity: r.qty,
        back_targets:
          r.target.kind === "emp"
            ? [{ employee_id: r.target.id, share_type: "percent", share_value: 100 }]
            : r.target.kind === "split"
              ? r.target.shares.map((s) => ({ employee_id: s.employee, share_type: "fixed", share_value: Number(s.amount) || 0 }))
              : [],
        register_bottle_keep: r.keep,
      }));
      const res = await api<BatchResult>(`/api/visits/${visitId}/lines/batch`, { method: "POST", idempotencyKey: idem.current, body: { items } });
      const ids = res.created_line_ids;
      const n = send.reduce((s, r) => s + r.qty, 0);
      setDraft({ rows: [], cat: null, productId: null });
      try {
        sessionStorage.removeItem(draftKey);
      } catch {
        /* ignore */
      }
      idem.current = newIdempotencyKey();
      await reloadFloor();
      toast.show({
        message: `${visit.table_name}に ${n}点 注文しました`,
        action: {
          label: "取り消す",
          onClick: async () => {
            try {
              await undoLines(visitId, ids);
              await reloadFloor();
              toast.show({ message: "いまの注文を取り消しました", tone: "info" });
              window.dispatchEvent(new Event("noxia:visit-changed"));
            } catch (e) {
              toast.show({ message: `取り消せませんでした: ${errorMessage(e)}`, tone: "error" });
            }
          },
        },
      });
      router.replace(`/visits/${visitId}`);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) setError(`${e.message} もう一度「確定」を押しても二重には登録されません。`);
      else setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }

  if (visitQ.loading || !visit) return visitQ.error ? <ErrorBox error={visitQ.error} onRetry={() => visitQ.reload(false)} /> : <Spinner label="伝票を読み込み中…" />;
  if (visit.status !== "open") {
    return (
      <div className="card space-y-3 max-w-2xl mx-auto">
        <p className="text-xl font-bold">この伝票は{visit.status === "closed" ? "会計済み" : "取消済み"}のため、注文できません。</p>
        <button className="btn btn-xl" onClick={() => router.replace(`/visits/${visitId}`)}>
          卓詳細へ
        </button>
      </div>
    );
  }

  const people = staffList ? staffs : casts;
  const colStyle = { background: "var(--color-panel)", border: "1.5px solid var(--color-border)", borderRadius: 14 } as const;
  const headCls = "pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]";

  return (
    <div className="flex flex-col gap-2" style={{ height: "calc(100dvh - 64px - 34px)", minHeight: 480 }}>
      <div className="flex items-center gap-3">
        <button className="btn btn-lg" onClick={() => router.replace(`/visits/${visitId}`)}>
          ← 卓詳細
        </button>
        <div>
          <div className="text-3xl font-bold leading-none">{visit.table_name} の注文</div>
          <div className="text-sm mt-0.5" style={{ color: "var(--color-muted)" }}>
            伝票#{visit.slip_number} ・ 在席 {visit.present_count}名
          </div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-sm" style={{ color: "var(--color-muted)" }}>
            現在の金額（税込・今回の注文は含まない）
          </div>
          <div className="text-3xl font-bold num leading-none">{yen(visit.quote?.total ?? "0")}</div>
        </div>
      </div>
      <ErrorBox error={products.error || categories.error || employees.error} />

      <div className="grid gap-2 flex-1 min-h-0" style={{ gridTemplateColumns: "150px minmax(0, 1fr) 200px minmax(0, 1.15fr)" }}>
        {/* 1. カテゴリー */}
        <section className="flex flex-col min-h-0" style={colStyle} aria-label="カテゴリー">
          <h2 className={headCls}>カテゴリー</h2>
          <div className="flex-1 overflow-y-auto p-1.5 space-y-1.5">
            {cats.map((c) => (
              <button key={c.id} className={`w-full rounded-xl px-2 text-left font-bold ${cat === c.id ? "pos-selected" : ""}`} style={{ minHeight: 56, ...(cat === c.id ? {} : { background: "var(--color-panel2)", border: "1px solid var(--color-border)" }) }} onClick={() => setDraft((d) => ({ ...d, cat: c.id, productId: null }))} aria-pressed={cat === c.id}>
                {c.name}
              </button>
            ))}
          </div>
        </section>

        {/* 2. メニューと単価 */}
        <section className="flex flex-col min-h-0" style={colStyle} aria-label="メニュー">
          <h2 className={headCls}>メニューと単価</h2>
          <div className="flex-1 overflow-y-auto p-1.5 space-y-1.5">
            {products.loading && <Spinner />}
            {shownProducts.length === 0 && !products.loading && <p className="p-2" style={{ color: "var(--color-muted)" }}>このカテゴリーに商品がありません。</p>}
            {shownProducts.map((p) => {
              const on = draft.productId === p.id;
              const inCart = rows.filter((r) => r.productId === p.id).reduce((s, r) => s + r.qty, 0);
              return (
                <button key={p.id} className={`w-full rounded-xl px-3 flex items-center justify-between gap-2 text-left ${on ? "pos-selected" : ""}`} style={{ minHeight: 56, ...(on ? {} : { background: "var(--color-panel2)", border: "1px solid var(--color-border)" }) }} onClick={() => setDraft((d) => ({ ...d, cat, productId: p.id }))} aria-pressed={on}>
                  <span className="font-bold leading-tight">
                    {p.name}
                    {inCart > 0 && <span className="badge badge-ok ml-2 num">{inCart}</span>}
                  </span>
                  <span className="num whitespace-nowrap">{yen(p.price)}</span>
                </button>
              );
            })}
          </div>
        </section>

        {/* 3. 誰に付けるか */}
        <section className="flex flex-col min-h-0" style={colStyle} aria-label="誰に付けるか">
          <h2 className={headCls}>誰に付けるか</h2>
          <div className="flex-1 overflow-y-auto p-1.5 space-y-1.5">
            {!selected && (
              <p className="text-sm px-1" style={{ color: "var(--color-muted)" }}>
                先にメニューを選んでください。
              </p>
            )}
            <button className="btn w-full" disabled={!selected} onClick={() => selected && addRow(selected.id, { kind: "customer" })}>
              お客様
            </button>
            <button className={`btn w-full ${staffList ? "btn-primary" : ""}`} onClick={() => setStaffList((v) => !v)} aria-pressed={staffList}>
              スタッフ{staffList ? " ▲" : " ▼"}
            </button>
            <button className="btn w-full" onClick={() => setDiscount(true)}>
              料金調整（値引き）
            </button>
            <button className="btn w-full" disabled={!selected || casts.length < 2} onClick={() => selected && setSplit({ productId: selected.id, rowKey: null })}>
              キャスト折半
            </button>
            <div className="text-xs font-bold px-1 pt-1" style={{ color: "var(--color-muted)" }}>
              {staffList ? "スタッフ（出勤順）" : "キャスト（出勤順）"}
            </div>
            {people.map((e) => (
              <button
                key={e.id}
                className="btn w-full justify-between"
                style={!isOnShift(e) ? { opacity: 0.65, borderStyle: "dashed" } : undefined}
                disabled={!selected}
                onClick={async () => {
                  if (!selected || !(await guard(e))) return;
                  addRow(selected.id, { kind: "emp", id: e.id });
                }}
                aria-label={e.display_name}
              >
                <span>{e.display_name}</span>
                {e.shift_status && !isOnShift(e) && <span className="text-xs font-normal">{SHIFT_LABEL[e.shift_status]}</span>}
              </button>
            ))}
          </div>
        </section>

        {/* 4. 今回の注文 */}
        <section className="flex flex-col min-h-0" style={colStyle} aria-label="今回の注文">
          <h2 className={`${headCls} flex items-center justify-between`}>
            <span>今回の注文</span>
            <span className="num">{count}点</span>
          </h2>
          <div className="flex-1 overflow-y-auto p-1.5 space-y-1.5">
            {rows.length === 0 && (
              <p className="p-2" style={{ color: "var(--color-muted)" }}>
                メニューを選び、「誰に付けるか」を押すと、ここに入ります。
              </p>
            )}
            {rows.map((r) => {
              const p = productMap.get(r.productId)!;
              return (
                <div key={r.key} className="rounded-xl px-2 py-1.5" style={{ background: "var(--color-panel2)", border: "1px solid var(--color-border)", opacity: r.qty === 0 ? 0.55 : 1 }} data-testid="order-row">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-bold leading-tight">{p.name}</span>
                    <span className="num text-sm whitespace-nowrap">{yen(p.price)}</span>
                  </div>
                  <div className="flex items-center gap-1.5 mt-1">
                    {r.target.kind === "split" ? (
                      <button className="badge badge-accent text-left" style={{ minHeight: 44, borderRadius: 10 }} onClick={() => setSplit({ productId: r.productId, rowKey: r.key })} aria-label="折半を編集">
                        {targetLabel(r.target)} ✎
                      </button>
                    ) : (
                      <span className="badge" style={{ fontSize: "0.95rem" }}>
                        {targetLabel(r.target)}
                      </span>
                    )}
                    {p.is_bottle_keepable && visit.customer && (
                      <button className={`badge ${r.keep ? "badge-ok" : ""}`} style={{ minHeight: 44 }} onClick={() => change((d) => ({ ...d, rows: d.rows.map((x) => (x.key === r.key ? { ...x, keep: !x.keep } : x)) }))}>
                        {r.keep ? "✓ キープする" : "キープ？"}
                      </button>
                    )}
                    <span className="ml-auto flex items-center gap-1.5">
                      <button className="btn btn-minus" style={{ minHeight: 46, minWidth: 46, padding: 0 }} onClick={() => setQty(r.key, -1)} disabled={r.qty === 0} aria-label="1つ減らす">
                        −
                      </button>
                      <span className="text-2xl font-bold num w-9 text-center" data-testid="order-qty">
                        {r.qty}
                      </span>
                      <button className="btn btn-plus" style={{ minHeight: 46, minWidth: 46, padding: 0 }} onClick={() => setQty(r.key, 1)} aria-label="1つ増やす">
                        ＋
                      </button>
                    </span>
                  </div>
                  {r.qty === 0 && (
                    <div className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
                      数量 0 のため注文しません
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="p-1.5 space-y-1.5 border-t" style={{ borderColor: "var(--color-border)" }}>
            {error && (
              <p className="font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {error}
              </p>
            )}
            <div className="flex gap-1.5">
              {rows.length > 0 && (
                <button className="btn" onClick={() => change((d) => ({ ...d, rows: [] }))}>
                  全部消す
                </button>
              )}
              <button className="btn btn-lg btn-primary flex-1" disabled={count === 0 || submitting} onClick={submit}>
                {submitting ? "送信中…" : count ? `確定（${count}点）` : "確定"}
              </button>
            </div>
          </div>
        </section>
      </div>

      {split && productMap.get(split.productId) && (
        <SplitBackPanel
          productName={productMap.get(split.productId)!.name}
          unitPrice={productMap.get(split.productId)!.price}
          casts={casts}
          initial={split.rowKey ? ((rows.find((r) => r.key === split.rowKey)?.target as { shares?: SplitShare[] })?.shares ?? []) : []}
          guard={guard}
          onCancel={() => setSplit(null)}
          onDone={(shares) => {
            if (split.rowKey) change((d) => ({ ...d, rows: d.rows.map((r) => (r.key === split.rowKey ? { ...r, target: { kind: "split", shares } } : r)) }));
            else addRow(split.productId, { kind: "split", shares });
            setSplit(null);
          }}
        />
      )}

      {discount && (
        <DiscountModal
          title="料金調整（値引き）"
          onClose={() => setDiscount(false)}
          onSubmit={async (amount, reason) => {
            const r = await api<BatchResult>(`/api/visits/${visitId}/lines/batch`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { items: [{ kind: "discount", amount, reason }] } });
            visitQ.setData(r);
            await reloadFloor();
            setDiscount(false);
            toast.show({ message: `${visit.table_name} ${yen(amount)} 値引きしました（${reason}）` });
          }}
        />
      )}
      {shiftGuard}
    </div>
  );
}
