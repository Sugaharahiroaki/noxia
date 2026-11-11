"use client";

import { useMemo, useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api, downloadFile, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, num, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Product } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Supplier = { id: number; name: string; contact: string; order_method: string; is_active: boolean; note: string };
type SupplierProduct = { id: number; supplier: number; supplier_name: string; product: number; product_name: string; purchase_price: string; order_unit_name: string; units_per_order_unit: string; lead_days: number; is_preferred: boolean };
type Candidate = {
  product_id: number;
  name: string;
  quantity_bottles: string;
  reorder_point: string;
  suggested_bottles: string;
  supplier_id: number | null;
  supplier_name: string | null;
  supplier_product_id: number | null;
  order_unit_name: string;
  units_per_order_unit: string;
  purchase_price: string | null;
};
type POLine = { id: number; product: number; product_name: string; supplier_product: number | null; order_units: string; units_per_order_unit: string; unit_price: string; expected_bottles: string; received_bottles: string; order_unit_name: string; note: string };
type PO = {
  id: number;
  number: number;
  supplier: number;
  supplier_name: string;
  status: "draft" | "confirmed" | "ordered" | "partially_received" | "received" | "cancelled";
  status_label: string;
  confirmed_at: string | null;
  ordered_at: string | null;
  ordered_via: string;
  expected_on: string | null;
  note: string;
  total_cost: string;
  lines: POLine[];
  receipts: { id: number; received_at: string; note: string; lines: { po_line: number; bottles: string }[] }[];
  created_at: string;
};

const STATUS_CLS: Record<PO["status"], string> = { draft: "", confirmed: "badge-accent", ordered: "badge-warn", partially_received: "badge-orange", received: "badge-ok", cancelled: "badge-danger" };

type DraftLine = { product_id: number; product_name: string; order_units: string; supplier_product_id: number | null; unit_price: string; order_unit_name: string; units_per_order_unit: string };

export default function PurchasingPage() {
  const { can } = useSession();
  const [tab, setTab] = useState<"orders" | "candidates" | "suppliers">("orders");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [draft, setDraft] = useState<{ supplier_id: number | null; lines: DraftLine[] } | null>(null);
  const [ordersKey, setOrdersKey] = useState(0);

  return (
    <div className="space-y-4">
      <PageHeader
        title="仕入先・発注管理"
        subtitle="発注は 下書き → オーナー確認 → 発注済み（仕入先へ手動連絡） → 入荷 の順に進みます。このアプリから仕入先へ自動送信は行いません。"
        actions={
          <>
            <SaveStatus state={save} />
            {(["orders", "candidates", "suppliers"] as const).map((t) => (
              <button key={t} className={`btn btn-sm ${tab === t ? "btn-primary" : ""}`} onClick={() => setTab(t)}>
                {{ orders: "発注一覧", candidates: "発注候補", suppliers: "仕入先" }[t]}
              </button>
            ))}
            {can("purchasing.draft") && (
              <button className="btn btn-primary" onClick={() => setDraft({ supplier_id: null, lines: [] })}>
                ＋ 発注の下書き
              </button>
            )}
          </>
        }
      />
      {tab === "orders" && <OrdersTab key={ordersKey} setSave={setSave} />}
      {tab === "candidates" && (
        <CandidatesTab
          canDraft={can("purchasing.draft")}
          onDraft={(supplierId, lines) => {
            setDraft({ supplier_id: supplierId, lines });
          }}
        />
      )}
      {tab === "suppliers" && <SuppliersTab canManage={can("inventory.manage")} setSave={setSave} />}
      {draft && (
        <DraftModal
          initial={draft}
          onClose={() => setDraft(null)}
          onSubmit={async (body) => {
            setSave({ kind: "saving", label: "下書きを作成中…" });
            try {
              await api("/api/inventory/purchase-orders", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved", label: "下書きを作成しました" });
              setDraft(null);
              setTab("orders");
              setOrdersKey((k) => k + 1);
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

// --- 発注一覧 -----------------------------------------------------------------
function OrdersTab({ setSave }: { setSave: (s: SaveState) => void }) {
  const { can } = useSession();
  const [status, setStatus] = useState("");
  const orders = useLoad<PO[]>(`/api/inventory/purchase-orders${status ? `?status=${status}` : ""}`);
  const [receiving, setReceiving] = useState<PO | null>(null);
  const [ordering, setOrdering] = useState<PO | null>(null);
  const [cancelling, setCancelling] = useState<PO | null>(null);
  const [exportText, setExportText] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  async function transition(po: PO, action: "confirm" | "mark-ordered" | "cancel", body: Record<string, unknown> = {}) {
    setSave({ kind: "saving" });
    try {
      await api(`/api/inventory/purchase-orders/${po.id}/${action}`, { method: "POST", idempotencyKey: newIdempotencyKey(), body });
      setSave({ kind: "saved" });
      await orders.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
      throw e;
    }
  }

  const statuses: { value: string; label: string }[] = [
    { value: "", label: "すべて" },
    { value: "draft", label: "下書き" },
    { value: "confirmed", label: "確認済" },
    { value: "ordered", label: "発注済" },
    { value: "partially_received", label: "一部入荷" },
    { value: "received", label: "入荷済" },
    { value: "cancelled", label: "取消" },
  ];

  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        {statuses.map((s) => (
          <button key={s.value} className={`btn btn-sm ${status === s.value ? "btn-primary" : ""}`} onClick={() => setStatus(s.value)}>
            {s.label}
          </button>
        ))}
      </div>
      <ErrorBox error={orders.error || err} onRetry={() => orders.reload(false)} />
      {orders.loading && <Spinner />}
      {orders.data?.length === 0 && <Empty>発注はありません</Empty>}
      {orders.data?.map((po) => (
        <div key={po.id} className="card space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold text-lg">発注#{po.number}</span>
            <span className="font-semibold">{po.supplier_name}</span>
            <span className={`badge ${STATUS_CLS[po.status]}`}>{po.status_label}</span>
            <span className="text-sm" style={{ color: "var(--color-muted)" }}>
              作成 {fmtDateTime(po.created_at)}
              {po.confirmed_at && ` ・ 確認 ${fmtDateTime(po.confirmed_at)}`}
              {po.ordered_at && ` ・ 発注 ${fmtDateTime(po.ordered_at)}（${po.ordered_via}）`}
              {po.expected_on && ` ・ 納品希望 ${fmtDate(po.expected_on)}`}
            </span>
            <span className="ml-auto num font-bold">{yen(po.total_cost)}</span>
          </div>
          <table className="table num">
            <thead>
              <tr>
                <th>商品</th>
                <th>発注数</th>
                <th>本数</th>
                <th>単価</th>
                <th>入荷済</th>
              </tr>
            </thead>
            <tbody>
              {po.lines.map((l) => (
                <tr key={l.id}>
                  <td>{l.product_name}</td>
                  <td>
                    {num(l.order_units, 2)} {l.order_unit_name}
                  </td>
                  <td>{num(l.expected_bottles, 2)} 本</td>
                  <td>{yen(l.unit_price)}</td>
                  <td style={{ color: Number(l.received_bottles) >= Number(l.expected_bottles) ? "var(--color-ok)" : undefined }}>
                    {num(l.received_bottles, 2)} / {num(l.expected_bottles, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {po.note && <div className="text-sm">備考: {po.note}</div>}
          <div className="flex flex-wrap gap-2">
            {po.status === "draft" && can("purchasing.confirm") && (
              <button className="btn btn-sm btn-primary" onClick={() => transition(po, "confirm").catch(() => {})}>
                オーナー確認（確定）
              </button>
            )}
            {po.status === "draft" && !can("purchasing.confirm") && (
              <span className="text-sm self-center" style={{ color: "var(--color-muted)" }}>
                オーナーの確認待ちです
              </span>
            )}
            {po.status === "confirmed" && can("purchasing.draft") && (
              <button className="btn btn-sm btn-primary" onClick={() => setOrdering(po)}>
                仕入先へ連絡済み → 発注済みにする
              </button>
            )}
            {(po.status === "ordered" || po.status === "partially_received" || po.status === "confirmed") && can("inventory.operate") && (
              <button className="btn btn-sm" onClick={() => setReceiving(po)}>
                入荷を登録
              </button>
            )}
            {po.status !== "cancelled" && po.status !== "received" && (
              <button
                className="btn btn-sm"
                onClick={async () => {
                  try {
                    const r = await api<{ text: string }>(`/api/inventory/purchase-orders/${po.id}/export`);
                    setExportText(r.text);
                  } catch (e) {
                    setErr(e);
                  }
                }}
              >
                発注内容をコピー
              </button>
            )}
            {can("export.inventory") && (
              <button className="btn btn-sm" onClick={() => downloadFile(`/api/inventory/purchase-orders/${po.id}/export?format=csv`).catch(setErr)}>
                CSV
              </button>
            )}
            {po.status !== "cancelled" && po.status !== "received" && po.receipts.length === 0 && can("purchasing.draft") && (
              <button className="btn btn-sm btn-ghost ml-auto" onClick={() => setCancelling(po)}>
                取消
              </button>
            )}
          </div>
          {po.receipts.length > 0 && (
            <details className="text-sm">
              <summary>入荷記録 {po.receipts.length} 件</summary>
              {po.receipts.map((r) => (
                <div key={r.id} className="card-2 mt-1">
                  {fmtDateTime(r.received_at)}: {r.lines.map((l) => `${po.lines.find((x) => x.id === l.po_line)?.product_name ?? ""} ${num(l.bottles, 2)}本`).join("、")}
                  {r.note && ` ・ ${r.note}`}
                </div>
              ))}
            </details>
          )}
        </div>
      ))}

      {ordering && (
        <MarkOrderedModal
          po={ordering}
          onClose={() => setOrdering(null)}
          onSubmit={async (via) => {
            await transition(ordering, "mark-ordered", { ordered_via: via });
            setOrdering(null);
          }}
        />
      )}
      {receiving && (
        <ReceiveModal
          po={receiving}
          onClose={() => setReceiving(null)}
          onSubmit={async (body) => {
            setSave({ kind: "saving", label: "入荷を登録中…" });
            try {
              await api(`/api/inventory/purchase-orders/${receiving.id}/receive`, { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved" });
              setReceiving(null);
              await orders.reload();
            } catch (e) {
              setSave({ kind: "error", error: e });
              throw e;
            }
          }}
        />
      )}
      {cancelling && (
        <ConfirmDialog
          title={`発注#${cancelling.number} を取り消す`}
          message="入荷前の発注だけ取り消せます。仕入先へ既に連絡済みの場合は、別途連絡してください。"
          confirmLabel="取り消す"
          danger
          onCancel={() => setCancelling(null)}
          onConfirm={async () => {
            try {
              await transition(cancelling, "cancel");
            } catch {
              /* 表示済み */
            }
            setCancelling(null);
          }}
        />
      )}
      {exportText !== null && (
        <Modal
          title="発注内容（コピー用）"
          onClose={() => setExportText(null)}
          footer={
            <>
              <button className="btn" onClick={() => setExportText(null)}>
                閉じる
              </button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(exportText);
                    setSave({ kind: "saved", label: "コピーしました" });
                  } catch {
                    setSave({ kind: "error", error: new Error("クリップボードにコピーできませんでした。テキストを選択してコピーしてください。") });
                  }
                }}
              >
                クリップボードにコピー
              </button>
            </>
          }
        >
          <textarea className="input" readOnly rows={10} value={exportText} />
          <p className="text-xs mt-2" style={{ color: "var(--color-muted)" }}>
            このテキストを電話・FAX・メール等で仕入先へ伝えてください。アプリからの自動送信は行いません。
          </p>
        </Modal>
      )}
    </div>
  );
}

function MarkOrderedModal({ po, onClose, onSubmit }: { po: PO; onClose: () => void; onSubmit: (via: string) => Promise<void> }) {
  const [via, setVia] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={`発注#${po.number} を発注済みにする`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !via.trim()}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit(via.trim());
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            発注済みにする
          </button>
        </>
      }
    >
      <p className="text-sm mb-3">
        仕入先（{po.supplier_name}）へ<strong>手動で</strong>発注内容を伝えたうえで記録します。下書きを作っただけでは「送信済み」になりません。
      </p>
      <Field label="連絡した手段（必須）">
        <input className="input" value={via} onChange={(e) => setVia(e.target.value)} placeholder="例: 電話 / FAX / メール / LINE" />
      </Field>
    </Modal>
  );
}

function ReceiveModal({ po, onClose, onSubmit }: { po: PO; onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const [qty, setQty] = useState<Record<number, string>>(() => Object.fromEntries(po.lines.map((l) => [l.id, String(Math.max(0, Number(l.expected_bottles) - Number(l.received_bottles)))])));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const any = Object.values(qty).some((v) => Number(v) > 0);
  return (
    <Modal
      title={`発注#${po.number} の入荷登録（部分入荷可）`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !any}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit({ note, lines: po.lines.map((l) => ({ po_line_id: l.id, bottles: Number(qty[l.id] || 0) })) });
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            入荷を登録する
          </button>
        </>
      }
    >
      <table className="table num">
        <thead>
          <tr>
            <th>商品</th>
            <th>発注本数</th>
            <th>入荷済</th>
            <th>今回入荷（本）</th>
          </tr>
        </thead>
        <tbody>
          {po.lines.map((l) => (
            <tr key={l.id}>
              <td>{l.product_name}</td>
              <td>{num(l.expected_bottles, 2)}</td>
              <td>{num(l.received_bottles, 2)}</td>
              <td>
                <input className="input num w-28" inputMode="decimal" value={qty[l.id] ?? ""} onChange={(e) => setQty({ ...qty, [l.id]: e.target.value.replace(/[^\d.]/g, "") })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Field label="メモ（任意）">
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

// --- 発注の下書き ---------------------------------------------------------------
function DraftModal({ initial, onClose, onSubmit }: { initial: { supplier_id: number | null; lines: DraftLine[] }; onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const suppliers = useLoad<Supplier[]>("/api/inventory/suppliers");
  const [supplierId, setSupplierId] = useState<number | null>(initial.supplier_id);
  const sps = useLoad<SupplierProduct[]>(supplierId ? `/api/inventory/supplier-products?supplier=${supplierId}` : null, [supplierId]);
  const products = useLoad<Product[]>("/api/store/products?active=1");
  const [lines, setLines] = useState<DraftLine[]>(initial.lines);
  const [note, setNote] = useState("");
  const [expected, setExpected] = useState("");
  const [addProduct, setAddProduct] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const total = lines.reduce((s, l) => s + Number(l.order_units || 0) * Number(l.unit_price || 0), 0);

  function addLine(productId: number) {
    const p = products.data?.find((x) => x.id === productId);
    if (!p || lines.some((l) => l.product_id === productId)) return;
    const sp = sps.data?.find((x) => x.product === productId);
    setLines([...lines, { product_id: p.id, product_name: p.name, order_units: "1", supplier_product_id: sp?.id ?? null, unit_price: sp ? String(Number(sp.purchase_price)) : "", order_unit_name: sp?.order_unit_name ?? "本", units_per_order_unit: sp ? String(Number(sp.units_per_order_unit)) : "1" }]);
  }

  return (
    <Modal
      title="発注の下書き"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !supplierId || lines.length === 0 || lines.some((l) => Number(l.order_units) <= 0)}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit({
                  supplier_id: supplierId,
                  note,
                  expected_on: expected || null,
                  lines: lines.map((l) => ({ product_id: l.product_id, order_units: Number(l.order_units), supplier_product_id: l.supplier_product_id, unit_price: l.unit_price === "" ? undefined : Number(l.unit_price) })),
                });
              } catch {
                /* 表示済み */
              } finally {
                setBusy(false);
              }
            }}
          >
            下書きを保存（送信はしません）
          </button>
        </>
      }
    >
      <ErrorBox error={suppliers.error || products.error} />
      <div className="space-y-3">
        <Field label="仕入先">
          <select className="input" value={supplierId ?? ""} onChange={(e) => setSupplierId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">選択してください</option>
            {suppliers.data
              ?.filter((s) => s.is_active)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
        </Field>
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <Field label="商品を追加">
              <select className="input" value={addProduct} onChange={(e) => setAddProduct(e.target.value ? Number(e.target.value) : "")}>
                <option value="">選択してください</option>
                {products.data
                  ?.filter((p) => p.stock_mode !== "none" && !lines.some((l) => l.product_id === p.id))
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {sps.data?.some((x) => x.product === p.id) ? "" : "（この仕入先の単価未登録）"}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          <button
            className="btn"
            disabled={addProduct === ""}
            onClick={() => {
              if (addProduct !== "") addLine(addProduct);
              setAddProduct("");
            }}
          >
            追加
          </button>
        </div>
        {lines.length === 0 && <Empty>発注明細がありません</Empty>}
        {lines.length > 0 && (
          <table className="table num">
            <thead>
              <tr>
                <th>商品</th>
                <th>発注数</th>
                <th>単位</th>
                <th>本数</th>
                <th>単価</th>
                <th>金額</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.product_id}>
                  <td>{l.product_name}</td>
                  <td>
                    <input className="input num w-24" inputMode="decimal" value={l.order_units} onChange={(e) => setLines(lines.map((x) => (x.product_id === l.product_id ? { ...x, order_units: e.target.value.replace(/[^\d.]/g, "") } : x)))} />
                  </td>
                  <td>{l.order_unit_name}</td>
                  <td>{num(Number(l.order_units || 0) * Number(l.units_per_order_unit || 1), 2)}</td>
                  <td>
                    <input className="input num w-28" inputMode="numeric" value={l.unit_price} onChange={(e) => setLines(lines.map((x) => (x.product_id === l.product_id ? { ...x, unit_price: e.target.value.replace(/[^\d.]/g, "") } : x)))} />
                  </td>
                  <td>{yen(Number(l.order_units || 0) * Number(l.unit_price || 0))}</td>
                  <td>
                    <button className="btn btn-sm btn-ghost" onClick={() => setLines(lines.filter((x) => x.product_id !== l.product_id))}>
                      削除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="text-right font-bold num">合計 {yen(total)}</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <Field label="納品希望日（任意）">
            <input className="input" type="date" value={expected} onChange={(e) => setExpected(e.target.value)} />
          </Field>
          <Field label="備考（任意）">
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

// --- 発注候補 -----------------------------------------------------------------
function CandidatesTab({ canDraft, onDraft }: { canDraft: boolean; onDraft: (supplierId: number | null, lines: DraftLine[]) => void }) {
  const cands = useLoad<Candidate[]>("/api/inventory/reorder-candidates");
  const grouped = useMemo(() => {
    const m = new Map<string, { supplier_id: number | null; supplier_name: string; rows: Candidate[] }>();
    for (const c of cands.data ?? []) {
      const key = String(c.supplier_id ?? "none");
      const g = m.get(key) ?? { supplier_id: c.supplier_id, supplier_name: c.supplier_name ?? "仕入先未設定", rows: [] };
      g.rows.push(c);
      m.set(key, g);
    }
    return [...m.values()];
  }, [cands.data]);

  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        発注点を下回った商品を、最低在庫まで補充する本数の目安とともに表示します（ショット販売の在庫は理論値）。
      </p>
      <ErrorBox error={cands.error} onRetry={() => cands.reload(false)} />
      {cands.loading && <Spinner />}
      {cands.data?.length === 0 && <Empty>発注候補はありません</Empty>}
      {grouped.map((g) => (
        <div key={String(g.supplier_id)} className="card space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="font-bold">{g.supplier_name}</h3>
            {canDraft && g.supplier_id && (
              <button
                className="btn btn-sm btn-primary ml-auto"
                onClick={() =>
                  onDraft(
                    g.supplier_id,
                    g.rows.map((c) => ({
                      product_id: c.product_id,
                      product_name: c.name,
                      order_units: String(Math.max(1, Math.ceil(Number(c.suggested_bottles) / Number(c.units_per_order_unit || 1)))),
                      supplier_product_id: c.supplier_product_id,
                      unit_price: c.purchase_price ? String(Number(c.purchase_price)) : "",
                      order_unit_name: c.order_unit_name,
                      units_per_order_unit: c.units_per_order_unit,
                    })),
                  )
                }
              >
                この仕入先で下書きを作成
              </button>
            )}
          </div>
          <table className="table num">
            <thead>
              <tr>
                <th>商品</th>
                <th>在庫</th>
                <th>発注点</th>
                <th>補充目安（本）</th>
                <th>発注単位</th>
                <th>仕入単価</th>
              </tr>
            </thead>
            <tbody>
              {g.rows.map((c) => (
                <tr key={c.product_id}>
                  <td className="font-semibold">{c.name}</td>
                  <td style={{ color: "var(--color-danger)" }}>{num(c.quantity_bottles, 2)}</td>
                  <td>{num(c.reorder_point, 2)}</td>
                  <td>{num(c.suggested_bottles)}</td>
                  <td>
                    {c.order_unit_name}（{num(c.units_per_order_unit, 2)}本）
                  </td>
                  <td>{c.purchase_price ? yen(c.purchase_price) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

// --- 仕入先 -------------------------------------------------------------------
type SupplierForm = { name: string; contact: string; order_method: string; is_active: boolean; note: string };

function SuppliersTab({ canManage, setSave }: { canManage: boolean; setSave: (s: SaveState) => void }) {
  const suppliers = useLoad<Supplier[]>("/api/inventory/suppliers");
  const [editing, setEditing] = useState<{ id: number | null; form: SupplierForm } | null>(null);
  const [itemsFor, setItemsFor] = useState<Supplier | null>(null);

  async function saveSupplier() {
    if (!editing) return;
    setSave({ kind: "saving" });
    try {
      if (editing.id === null) await api("/api/inventory/suppliers", { method: "POST", body: editing.form, idempotencyKey: newIdempotencyKey() });
      else await api(`/api/inventory/suppliers/${editing.id}`, { method: "PATCH", body: editing.form });
      setSave({ kind: "saved" });
      setEditing(null);
      await suppliers.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      {canManage && (
        <button className="btn btn-primary" onClick={() => setEditing({ id: null, form: { name: "", contact: "", order_method: "", is_active: true, note: "" } })}>
          ＋ 仕入先を追加
        </button>
      )}
      <ErrorBox error={suppliers.error} onRetry={() => suppliers.reload(false)} />
      {suppliers.loading && <Spinner />}
      {suppliers.data?.length === 0 && <Empty>仕入先が登録されていません</Empty>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {suppliers.data?.map((s) => (
          <div key={s.id} className="card-2 space-y-1" style={{ opacity: s.is_active ? 1 : 0.6 }}>
            <div className="flex items-center gap-2">
              <span className="font-bold">{s.name}</span>
              {!s.is_active && <span className="badge badge-danger">停止</span>}
            </div>
            <div className="text-sm" style={{ color: "var(--color-muted)" }}>
              {s.contact && `連絡先 ${s.contact}`}
              {s.order_method && ` ・ 発注方法 ${s.order_method}`}
            </div>
            {s.note && <div className="text-sm">{s.note}</div>}
            <div className="flex gap-2 pt-1">
              <button className="btn btn-sm" onClick={() => setItemsFor(s)}>
                取扱商品・仕入単価
              </button>
              {canManage && (
                <button className="btn btn-sm" onClick={() => setEditing({ id: s.id, form: { name: s.name, contact: s.contact, order_method: s.order_method, is_active: s.is_active, note: s.note } })}>
                  編集
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <Modal
          title={editing.id === null ? "仕入先を追加" : "仕入先を編集"}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={saveSupplier} disabled={!editing.form.name.trim()}>
                保存
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="仕入先名（必須）">
              <input className="input" value={editing.form.name} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, name: e.target.value } })} />
            </Field>
            <Field label="連絡先">
              <input className="input" value={editing.form.contact} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, contact: e.target.value } })} />
            </Field>
            <Field label="発注方法メモ（電話・FAX・LINE 等。自動送信はしません）">
              <input className="input" value={editing.form.order_method} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, order_method: e.target.value } })} />
            </Field>
            <Field label="メモ">
              <textarea className="input" value={editing.form.note} onChange={(e) => setEditing({ ...editing, form: { ...editing.form, note: e.target.value } })} />
            </Field>
            <Toggle checked={editing.form.is_active} onChange={(v) => setEditing({ ...editing, form: { ...editing.form, is_active: v } })} label="有効" />
          </div>
        </Modal>
      )}
      {itemsFor && <SupplierItemsModal supplier={itemsFor} canManage={canManage} onClose={() => setItemsFor(null)} setSave={setSave} />}
    </div>
  );
}

function SupplierItemsModal({ supplier, canManage, onClose, setSave }: { supplier: Supplier; canManage: boolean; onClose: () => void; setSave: (s: SaveState) => void }) {
  const items = useLoad<SupplierProduct[]>(`/api/inventory/supplier-products?supplier=${supplier.id}`);
  const products = useLoad<Product[]>("/api/store/products?active=1");
  const [form, setForm] = useState<{ id: number | null; product: number | null; purchase_price: string; order_unit_name: string; units_per_order_unit: string; lead_days: string; is_preferred: boolean } | null>(null);

  async function saveItem() {
    if (!form || !form.product) return;
    const body = { supplier: supplier.id, product: form.product, purchase_price: form.purchase_price || "0", order_unit_name: form.order_unit_name || "本", units_per_order_unit: form.units_per_order_unit || "1", lead_days: Number(form.lead_days || 0), is_preferred: form.is_preferred };
    setSave({ kind: "saving" });
    try {
      if (form.id === null) await api("/api/inventory/supplier-products", { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      else await api(`/api/inventory/supplier-products/${form.id}`, { method: "PATCH", body });
      setSave({ kind: "saved" });
      setForm(null);
      await items.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <Modal title={`${supplier.name} の取扱商品・仕入単価`} wide onClose={onClose}>
      <ErrorBox error={items.error} />
      {items.loading && <Spinner />}
      {canManage && (
        <button className="btn btn-sm btn-primary mb-2" onClick={() => setForm({ id: null, product: null, purchase_price: "", order_unit_name: "本", units_per_order_unit: "1", lead_days: "0", is_preferred: false })}>
          ＋ 商品を追加
        </button>
      )}
      {items.data?.length === 0 && <Empty>取扱商品が登録されていません</Empty>}
      <table className="table num">
        <thead>
          <tr>
            <th>商品</th>
            <th>仕入単価（発注単位あたり）</th>
            <th>発注単位</th>
            <th>本数/単位</th>
            <th>リード日数</th>
            <th>優先</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.data?.map((sp) => (
            <tr key={sp.id}>
              <td>{sp.product_name}</td>
              <td>{yen(sp.purchase_price)}</td>
              <td>{sp.order_unit_name}</td>
              <td>{num(sp.units_per_order_unit, 2)}</td>
              <td>{sp.lead_days}</td>
              <td>{sp.is_preferred ? "★" : ""}</td>
              <td>
                {canManage && (
                  <button className="btn btn-sm" onClick={() => setForm({ id: sp.id, product: sp.product, purchase_price: String(Number(sp.purchase_price)), order_unit_name: sp.order_unit_name, units_per_order_unit: String(Number(sp.units_per_order_unit)), lead_days: String(sp.lead_days), is_preferred: sp.is_preferred })}>
                    編集
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {form && (
        <div className="card-2 mt-3 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <Field label="商品">
              <select className="input" value={form.product ?? ""} disabled={form.id !== null} onChange={(e) => setForm({ ...form, product: e.target.value ? Number(e.target.value) : null })}>
                <option value="">選択してください</option>
                {products.data
                  ?.filter((p) => p.stock_mode !== "none")
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="仕入単価（発注単位あたり・円）">
              <input className="input num" inputMode="numeric" value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value.replace(/[^\d.]/g, "") })} />
            </Field>
            <Field label="発注単位名（ケース等）">
              <input className="input" value={form.order_unit_name} onChange={(e) => setForm({ ...form, order_unit_name: e.target.value })} />
            </Field>
            <Field label="発注単位あたりの入荷本数">
              <input className="input num" inputMode="decimal" value={form.units_per_order_unit} onChange={(e) => setForm({ ...form, units_per_order_unit: e.target.value.replace(/[^\d.]/g, "") })} />
            </Field>
            <Field label="リード日数">
              <input className="input num" inputMode="numeric" value={form.lead_days} onChange={(e) => setForm({ ...form, lead_days: e.target.value.replace(/[^\d]/g, "") })} />
            </Field>
            <Toggle checked={form.is_preferred} onChange={(v) => setForm({ ...form, is_preferred: v })} label="優先仕入先" />
          </div>
          <div className="flex gap-2 justify-end">
            <button className="btn btn-sm" onClick={() => setForm(null)}>
              キャンセル
            </button>
            <button className="btn btn-sm btn-primary" onClick={saveItem} disabled={!form.product}>
              保存
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
