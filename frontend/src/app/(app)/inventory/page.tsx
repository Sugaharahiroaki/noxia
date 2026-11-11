"use client";

import { useMemo, useState } from "react";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api, downloadFile, newIdempotencyKey } from "@/lib/api";
import { fmtDateTime, num, todayJst, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Category, Product } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type StockRow = {
  product_id: number;
  name: string;
  category: string;
  stock_mode: "bottle" | "shot";
  is_active: boolean;
  quantity_bottles: string;
  is_theoretical: boolean;
  volume_ml: string | null;
  shot_ml: string | null;
  shots_remaining_est: string | null;
  reorder_point: string | null;
  min_stock: string | null;
  low_stock: boolean;
  cost_price: string | null;
};

type Movement = {
  id: number;
  product: number;
  product_name: string;
  kind: string;
  kind_label: string;
  quantity: string;
  is_theoretical: boolean;
  order_line: number | null;
  reference: string;
  reason: string;
  occurred_at: string;
  performed_by_name: string;
};

type StockTakeLine = { id: number; product: number; product_name: string; theoretical_qty: string; counted_full_bottles: string; counted_open_ml: string; counted_qty: string; difference: string; note: string };
type StockTake = { id: number; taken_at: string; status: string; applied_at: string | null; performed_by_name: string; note: string; lines: StockTakeLine[]; created_at: string };

const MOVEMENT_KINDS = [
  { kind: "receive", label: "入荷（発注外）" },
  { kind: "waste", label: "廃棄" },
  { kind: "adjust", label: "調整（＋/−）" },
  { kind: "keep_out", label: "キープへ移管（販売在庫→キープ）" },
];

const CATEGORY_KINDS: { value: string; label: string }[] = [
  { value: "drink", label: "ドリンク" },
  { value: "cast_drink", label: "キャストドリンク" },
  { value: "bottle", label: "ボトル" },
  { value: "shot", label: "ショット" },
  { value: "food", label: "フード" },
  { value: "other", label: "その他" },
];

const STOCK_MODES: { value: Product["stock_mode"]; label: string }[] = [
  { value: "none", label: "在庫管理しない" },
  { value: "bottle", label: "本数管理（シャンパン等）" },
  { value: "shot", label: "ショット販売（容量から理論消費）" },
];

export default function InventoryPage() {
  const { can } = useSession();
  const canOperate = can("inventory.operate");
  const canCatalog = can("catalog.edit");
  const [tab, setTab] = useState<"stock" | "movements" | "stocktake" | "products">("stock");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [exportErr, setExportErr] = useState<unknown>(null);

  return (
    <div className="space-y-4">
      <PageHeader
        title="商品・在庫・棚卸し"
        subtitle="本数管理（ボトル）とショット販売（容量・1杯量からの理論消費）を区別します。ショットの在庫は理論値で、棚卸しで実在庫との差を調整します。"
        actions={
          <>
            <SaveStatus state={save} />
            {(["stock", "movements", "stocktake", "products"] as const).map((t) => (
              <button key={t} className={`btn btn-sm ${tab === t ? "btn-primary" : ""}`} onClick={() => setTab(t)}>
                {{ stock: "在庫", movements: "入出庫履歴", stocktake: "棚卸し", products: "商品マスタ" }[t]}
              </button>
            ))}
            {can("export.inventory") && (
              <button
                className="btn btn-sm"
                onClick={() => {
                  const to = todayJst();
                  const from = to.slice(0, 7) + "-01";
                  downloadFile(`/api/exports/inventory.csv?from=${from}&to=${to}`).catch(setExportErr);
                }}
              >
                今月の在庫 CSV
              </button>
            )}
          </>
        }
      />
      <ErrorBox error={exportErr} />
      {tab === "stock" && <StockTab canOperate={canOperate} setSave={setSave} />}
      {tab === "movements" && <MovementsTab />}
      {tab === "stocktake" && <StockTakeTab canOperate={canOperate} setSave={setSave} />}
      {tab === "products" && <ProductsTab canEdit={canCatalog} setSave={setSave} />}
    </div>
  );
}

// --- 在庫 ---------------------------------------------------------------------
function StockTab({ canOperate, setSave }: { canOperate: boolean; setSave: (s: SaveState) => void }) {
  const stock = useLoad<StockRow[]>("/api/inventory/stock");
  const [mv, setMv] = useState<StockRow | null>(null);
  const low = (stock.data ?? []).filter((r) => r.low_stock && r.is_active);
  return (
    <div className="space-y-3">
      <ErrorBox error={stock.error} onRetry={() => stock.reload(false)} />
      {stock.loading && <Spinner />}
      {low.length > 0 && (
        <div className="card-2" style={{ borderColor: "var(--color-warn)" }}>
          <span className="font-semibold">発注点を下回っている商品: </span>
          {low.map((r) => r.name).join("、")}（「仕入先・発注」の発注候補から下書きを作れます）
        </div>
      )}
      {stock.data?.length === 0 && <Empty>在庫管理対象の商品がありません。商品マスタで「本数管理」または「ショット販売」を設定してください。</Empty>}
      <div className="scroll-x">
        <table className="table num">
          <thead>
            <tr>
              <th>商品</th>
              <th>カテゴリ</th>
              <th>管理方法</th>
              <th>在庫（本）</th>
              <th>残りショット（推定）</th>
              <th>発注点 / 最低在庫</th>
              <th>原価</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {stock.data?.map((r) => (
              <tr key={r.product_id} style={{ opacity: r.is_active ? 1 : 0.5 }}>
                <td className="font-semibold">
                  {r.name}
                  {!r.is_active && <span className="badge ml-2">販売停止</span>}
                </td>
                <td>{r.category}</td>
                <td>
                  {r.stock_mode === "shot" ? (
                    <span className="badge badge-accent">ショット {num(r.volume_ml)}ml / 1杯 {num(r.shot_ml)}ml</span>
                  ) : (
                    <span className="badge">本数</span>
                  )}
                </td>
                <td style={r.low_stock ? { color: "var(--color-danger)", fontWeight: 700 } : undefined}>
                  {num(r.quantity_bottles, 2)}
                  {r.is_theoretical && (
                    <span className="badge badge-warn ml-2" title="販売数量からの理論値です。棚卸しで実在庫に合わせてください。">
                      理論値
                    </span>
                  )}
                  {r.low_stock && <span className="badge badge-danger ml-2">要発注</span>}
                </td>
                <td>{r.shots_remaining_est ? `約 ${num(r.shots_remaining_est)} 杯（推定）` : "—"}</td>
                <td>
                  {r.reorder_point ? num(r.reorder_point, 2) : "—"} / {r.min_stock ? num(r.min_stock, 2) : "—"}
                </td>
                <td>{r.cost_price ? yen(r.cost_price) : <span style={{ color: "var(--color-muted)" }}>未設定</span>}</td>
                <td>
                  {canOperate && (
                    <button className="btn btn-sm" onClick={() => setMv(r)}>
                      入出庫
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mv && (
        <MovementModal
          row={mv}
          onClose={() => setMv(null)}
          onSubmit={async (body) => {
            setSave({ kind: "saving" });
            try {
              await api("/api/inventory/movements", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved" });
              setMv(null);
              await stock.reload();
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

function MovementModal({ row, onClose, onSubmit }: { row: StockRow; onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const [kind, setKind] = useState("receive");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const needsReason = kind !== "receive";
  return (
    <Modal
      title={`${row.name} の入出庫`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            キャンセル
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !qty || Number(qty) === 0 || (needsReason && !reason.trim())}
            onClick={async () => {
              setBusy(true);
              try {
                await onSubmit({ product_id: row.product_id, kind, quantity: Number(qty), reason: reason.trim() });
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
        <p className="text-sm num" style={{ color: "var(--color-muted)" }}>
          現在の在庫 {num(row.quantity_bottles, 2)} 本{row.is_theoretical && "（理論値）"}
        </p>
        <div className="grid grid-cols-2 gap-2">
          {MOVEMENT_KINDS.map((k) => (
            <button key={k.kind} className={`btn btn-sm ${kind === k.kind ? "btn-primary" : ""}`} onClick={() => setKind(k.kind)} aria-pressed={kind === k.kind}>
              {k.label}
            </button>
          ))}
        </div>
        <Field label={kind === "adjust" ? "増減（本。減らす場合はマイナス）" : "本数"} hint={row.stock_mode === "shot" ? "小数可（例: 0.5 = 半分）" : undefined}>
          <input className="input input-lg" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value.replace(/[^\d.\-]/g, ""))} />
        </Field>
        <Field label={needsReason ? "理由（必須）" : "理由・参照（任意）"}>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        {kind === "keep_out" && (
          <p className="text-xs" style={{ color: "var(--color-muted)" }}>
            販売在庫からお客様のキープへ移すときに使います（キープ側の登録は顧客画面で行います）。
          </p>
        )}
      </div>
    </Modal>
  );
}

// --- 入出庫履歴 ----------------------------------------------------------------
function MovementsTab() {
  const products = useLoad<Product[]>("/api/store/products");
  const [productId, setProductId] = useState<number | null>(null);
  const moves = useLoad<Movement[]>(`/api/inventory/movements${productId ? `?product=${productId}` : ""}`);
  return (
    <div className="space-y-3">
      <div className="max-w-md">
        <Field label="商品で絞り込む">
          <select className="input" value={productId ?? ""} onChange={(e) => setProductId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">すべて</option>
            {products.data
              ?.filter((p) => p.stock_mode !== "none")
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </Field>
      </div>
      <ErrorBox error={moves.error} onRetry={() => moves.reload(false)} />
      {moves.loading && <Spinner />}
      {moves.data?.length === 0 && <Empty>入出庫の記録はありません</Empty>}
      <div className="scroll-x">
        <table className="table num">
          <thead>
            <tr>
              <th>日時</th>
              <th>商品</th>
              <th>種別</th>
              <th>増減（本）</th>
              <th>参照</th>
              <th>理由</th>
              <th>操作者</th>
            </tr>
          </thead>
          <tbody>
            {moves.data?.map((m) => (
              <tr key={m.id}>
                <td className="whitespace-nowrap">{fmtDateTime(m.occurred_at)}</td>
                <td>{m.product_name}</td>
                <td>
                  <span className="badge">{m.kind_label}</span>
                </td>
                <td style={{ color: Number(m.quantity) < 0 ? "var(--color-danger)" : "var(--color-ok)" }}>
                  {Number(m.quantity) > 0 ? "+" : ""}
                  {num(m.quantity, 4)}
                  {m.is_theoretical && <span className="badge badge-warn ml-1">理論</span>}
                </td>
                <td>{m.reference}</td>
                <td>{m.reason}</td>
                <td>{m.performed_by_name}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- 棚卸し -------------------------------------------------------------------
function StockTakeTab({ canOperate, setSave }: { canOperate: boolean; setSave: (s: SaveState) => void }) {
  const takes = useLoad<StockTake[]>("/api/inventory/stock-takes");
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-3">
      {canOperate && (
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          ＋ 棚卸しを行う
        </button>
      )}
      <ErrorBox error={takes.error} onRetry={() => takes.reload(false)} />
      {takes.loading && <Spinner />}
      {takes.data?.length === 0 && <Empty>棚卸しの記録はありません</Empty>}
      {takes.data?.map((t) => (
        <details key={t.id} className="card-2">
          <summary className="cursor-pointer flex flex-wrap items-center gap-2">
            <span className="font-semibold">{fmtDateTime(t.taken_at)}</span>
            <span className={`badge ${t.status === "applied" ? "badge-ok" : "badge-warn"}`}>{t.status === "applied" ? "調整反映済" : "下書き（未反映）"}</span>
            <span className="text-sm" style={{ color: "var(--color-muted)" }}>
              {t.lines.length} 品目 {t.performed_by_name && `・ ${t.performed_by_name}`} {t.note && `・ ${t.note}`}
            </span>
          </summary>
          <table className="table num mt-2">
            <thead>
              <tr>
                <th>商品</th>
                <th>理論在庫</th>
                <th>実在庫（本換算）</th>
                <th>差</th>
                <th>メモ</th>
              </tr>
            </thead>
            <tbody>
              {t.lines.map((l) => (
                <tr key={l.id}>
                  <td>{l.product_name}</td>
                  <td>{num(l.theoretical_qty, 2)}</td>
                  <td>
                    {num(l.counted_qty, 2)}
                    <span className="text-xs ml-1" style={{ color: "var(--color-muted)" }}>
                      （{num(l.counted_full_bottles, 2)}本 + 開封 {num(l.counted_open_ml)}ml）
                    </span>
                  </td>
                  <td style={{ color: Number(l.difference) === 0 ? undefined : "var(--color-danger)" }}>
                    {Number(l.difference) > 0 ? "+" : ""}
                    {num(l.difference, 2)}
                  </td>
                  <td>{l.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
      {open && (
        <StockTakeModal
          onClose={() => setOpen(false)}
          onSubmit={async (body) => {
            setSave({ kind: "saving", label: "棚卸しを保存中…" });
            try {
              await api("/api/inventory/stock-takes", { method: "POST", idempotencyKey: newIdempotencyKey(), body });
              setSave({ kind: "saved" });
              setOpen(false);
              await takes.reload();
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

function StockTakeModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (body: Record<string, unknown>) => Promise<void> }) {
  const stock = useLoad<StockRow[]>("/api/inventory/stock");
  const [counts, setCounts] = useState<Record<number, { full: string; open: string; note: string }>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const rows = (stock.data ?? []).filter((r) => r.is_active);
  const entered = rows.filter((r) => counts[r.product_id] && (counts[r.product_id].full !== "" || counts[r.product_id].open !== ""));

  function countedQty(r: StockRow): number | null {
    const c = counts[r.product_id];
    if (!c || (c.full === "" && c.open === "")) return null;
    let q = Number(c.full || 0);
    if (c.open && r.volume_ml) q += Number(c.open) / Number(r.volume_ml);
    return q;
  }

  async function submit(apply: boolean) {
    setBusy(true);
    try {
      await onSubmit({
        note,
        apply,
        lines: entered.map((r) => ({ product_id: r.product_id, counted_full_bottles: Number(counts[r.product_id].full || 0), counted_open_ml: Number(counts[r.product_id].open || 0), note: counts[r.product_id].note })),
      });
    } catch {
      /* 表示済み */
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="棚卸し（実在庫を入力）"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            キャンセル
          </button>
          <button className="btn" onClick={() => submit(false)} disabled={busy || entered.length === 0}>
            下書き保存（在庫は変えない）
          </button>
          <button className="btn btn-primary" onClick={() => submit(true)} disabled={busy || entered.length === 0}>
            差を在庫に反映して確定
          </button>
        </>
      }
    >
      <p className="text-sm mb-3" style={{ color: "var(--color-muted)" }}>
        入力した品目だけが記録されます。理論在庫（販売からの推定）と実在庫の差が「棚卸調整」として記録されます。ショット販売の商品は開封ボトルの残量(ml)も入力できます。
      </p>
      <ErrorBox error={stock.error} />
      {stock.loading && <Spinner />}
      <div className="scroll-x">
        <table className="table num">
          <thead>
            <tr>
              <th>商品</th>
              <th>理論在庫</th>
              <th>未開封（本）</th>
              <th>開封残量（ml 合計）</th>
              <th>実在庫（本換算）</th>
              <th>差</th>
              <th>メモ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const c = counts[r.product_id] ?? { full: "", open: "", note: "" };
              const q = countedQty(r);
              const diff = q === null ? null : q - Number(r.quantity_bottles);
              return (
                <tr key={r.product_id}>
                  <td className="font-semibold">{r.name}</td>
                  <td>
                    {num(r.quantity_bottles, 2)}
                    {r.is_theoretical && <span className="badge badge-warn ml-1">理論</span>}
                  </td>
                  <td>
                    <input className="input num w-24" inputMode="decimal" value={c.full} onChange={(e) => setCounts({ ...counts, [r.product_id]: { ...c, full: e.target.value.replace(/[^\d.]/g, "") } })} />
                  </td>
                  <td>
                    {r.stock_mode === "shot" && r.volume_ml ? (
                      <input className="input num w-28" inputMode="decimal" value={c.open} onChange={(e) => setCounts({ ...counts, [r.product_id]: { ...c, open: e.target.value.replace(/[^\d.]/g, "") } })} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{q === null ? "—" : num(q, 2)}</td>
                  <td style={{ color: diff === null || Math.abs(diff) < 0.0001 ? undefined : "var(--color-danger)" }}>{diff === null ? "—" : `${diff > 0 ? "+" : ""}${num(diff, 2)}`}</td>
                  <td>
                    <input className="input w-32" value={c.note} onChange={(e) => setCounts({ ...counts, [r.product_id]: { ...c, note: e.target.value } })} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Field label="メモ（任意）">
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

// --- 商品マスタ ---------------------------------------------------------------
type ProductForm = {
  category: number | null;
  name: string;
  price: string;
  tax_rate: string;
  is_active: boolean;
  sort_order: string;
  stock_mode: Product["stock_mode"];
  volume_ml: string;
  shot_ml: string;
  reorder_point: string;
  min_stock: string;
  cost_price: string;
  is_bottle_keepable: boolean;
  note: string;
};

const emptyProduct: ProductForm = { category: null, name: "", price: "", tax_rate: "10", is_active: true, sort_order: "0", stock_mode: "none", volume_ml: "", shot_ml: "", reorder_point: "", min_stock: "", cost_price: "", is_bottle_keepable: false, note: "" };

function toForm(p: Product): ProductForm {
  return {
    category: p.category,
    name: p.name,
    price: String(Number(p.price)),
    tax_rate: String(Number(p.tax_rate)),
    is_active: p.is_active,
    sort_order: String(p.sort_order),
    stock_mode: p.stock_mode,
    volume_ml: p.volume_ml ? String(Number(p.volume_ml)) : "",
    shot_ml: p.shot_ml ? String(Number(p.shot_ml)) : "",
    reorder_point: p.reorder_point ? String(Number(p.reorder_point)) : "",
    min_stock: p.min_stock ? String(Number(p.min_stock)) : "",
    cost_price: p.cost_price ? String(Number(p.cost_price)) : "",
    is_bottle_keepable: p.is_bottle_keepable,
    note: p.note,
  };
}

function ProductsTab({ canEdit, setSave }: { canEdit: boolean; setSave: (s: SaveState) => void }) {
  const products = useLoad<Product[]>("/api/store/products");
  const categories = useLoad<Category[]>("/api/store/categories");
  const [editing, setEditing] = useState<{ id: number | null; form: ProductForm } | null>(null);
  const [catEditing, setCatEditing] = useState<{ id: number | null; name: string; kind: string; sort_order: string; is_active: boolean } | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const grouped = useMemo(() => {
    const cats = categories.data ?? [];
    return cats.map((c) => ({ cat: c, items: (products.data ?? []).filter((p) => p.category === c.id && (showInactive || p.is_active)) }));
  }, [products.data, categories.data, showInactive]);

  async function saveProduct() {
    if (!editing) return;
    const f = editing.form;
    const body = {
      category: f.category,
      name: f.name.trim(),
      price: f.price || "0",
      tax_rate: f.tax_rate || "10",
      is_active: f.is_active,
      sort_order: Number(f.sort_order || 0),
      stock_mode: f.stock_mode,
      volume_ml: f.volume_ml || null,
      shot_ml: f.shot_ml || null,
      reorder_point: f.reorder_point || null,
      min_stock: f.min_stock || null,
      cost_price: f.cost_price || null,
      is_bottle_keepable: f.is_bottle_keepable,
      note: f.note,
    };
    setSave({ kind: "saving" });
    try {
      if (editing.id === null) await api("/api/store/products", { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      else await api(`/api/store/products/${editing.id}`, { method: "PATCH", body });
      setSave({ kind: "saved" });
      setEditing(null);
      await products.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  async function saveCategory() {
    if (!catEditing) return;
    const body = { name: catEditing.name.trim(), kind: catEditing.kind, sort_order: Number(catEditing.sort_order || 0), is_active: catEditing.is_active };
    setSave({ kind: "saving" });
    try {
      if (catEditing.id === null) await api("/api/store/categories", { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      else await api(`/api/store/categories/${catEditing.id}`, { method: "PATCH", body });
      setSave({ kind: "saved" });
      setCatEditing(null);
      await categories.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        価格・税率を変更しても、過去の注文金額は変わりません（取引時点の情報を伝票に保存しています）。削除はせず「販売停止」にします。
      </p>
      <div className="flex flex-wrap gap-2 items-center">
        {canEdit && (
          <>
            <button className="btn btn-primary" onClick={() => setEditing({ id: null, form: { ...emptyProduct, category: categories.data?.[0]?.id ?? null } })} disabled={!categories.data?.length}>
              ＋ 商品を追加
            </button>
            <button className="btn" onClick={() => setCatEditing({ id: null, name: "", kind: "drink", sort_order: "0", is_active: true })}>
              ＋ カテゴリを追加
            </button>
          </>
        )}
        <label className="flex items-center gap-2 ml-auto text-sm">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
          販売停止も表示
        </label>
      </div>
      <ErrorBox error={products.error || categories.error} onRetry={() => products.reload(false)} />
      {(products.loading || categories.loading) && <Spinner />}
      {categories.data?.length === 0 && <Empty>カテゴリがありません。先にカテゴリを追加してください。</Empty>}
      {grouped.map(({ cat, items }) => (
        <section key={cat.id} className="card space-y-2" style={{ opacity: cat.is_active ? 1 : 0.6 }}>
          <div className="flex items-center gap-2">
            <h3 className="font-bold">{cat.name}</h3>
            <span className="badge">{CATEGORY_KINDS.find((k) => k.value === cat.kind)?.label ?? cat.kind}</span>
            {!cat.is_active && <span className="badge badge-danger">停止</span>}
            {canEdit && (
              <button className="btn btn-sm ml-auto" onClick={() => setCatEditing({ id: cat.id, name: cat.name, kind: cat.kind, sort_order: String(cat.sort_order), is_active: cat.is_active })}>
                カテゴリ編集
              </button>
            )}
          </div>
          {items.length === 0 && <Empty>商品がありません</Empty>}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {items.map((p) => (
              <div key={p.id} className="card-2 flex items-start gap-2" style={{ opacity: p.is_active ? 1 : 0.5 }}>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold">
                    {p.name}
                    {!p.is_active && <span className="badge badge-danger ml-2">販売停止</span>}
                  </div>
                  <div className="text-sm num" style={{ color: "var(--color-muted)" }}>
                    {yen(p.price)} ・ 税率 {Number(p.tax_rate)}% ・ {STOCK_MODES.find((m) => m.value === p.stock_mode)?.label}
                    {p.stock_mode === "shot" && ` ${num(p.volume_ml)}ml / ${num(p.shot_ml)}ml`}
                    {p.is_bottle_keepable && " ・ キープ可"}
                    {p.cost_price && ` ・ 原価 ${yen(p.cost_price)}`}
                  </div>
                </div>
                {canEdit && (
                  <button className="btn btn-sm" onClick={() => setEditing({ id: p.id, form: toForm(p) })}>
                    編集
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      {editing && (
        <Modal
          title={editing.id === null ? "商品を追加" : "商品を編集"}
          wide
          onClose={() => setEditing(null)}
          footer={
            <>
              {editing.id !== null && editing.form.is_active && (
                <DeactivateProductButton
                  id={editing.id}
                  name={editing.form.name}
                  onDone={() => {
                    setEditing(null);
                    products.reload();
                  }}
                />
              )}
              <button className="btn" onClick={() => setEditing(null)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={saveProduct} disabled={!editing.form.name.trim() || !editing.form.category}>
                保存
              </button>
            </>
          }
        >
          <ProductFormFields form={editing.form} categories={categories.data ?? []} onChange={(f) => setEditing({ ...editing, form: f })} />
        </Modal>
      )}

      {catEditing && (
        <Modal
          title={catEditing.id === null ? "カテゴリを追加" : "カテゴリを編集"}
          onClose={() => setCatEditing(null)}
          footer={
            <>
              <button className="btn" onClick={() => setCatEditing(null)}>
                キャンセル
              </button>
              <button className="btn btn-primary" onClick={saveCategory} disabled={!catEditing.name.trim()}>
                保存
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="カテゴリ名">
              <input className="input" value={catEditing.name} onChange={(e) => setCatEditing({ ...catEditing, name: e.target.value })} />
            </Field>
            <Field label="種別（バック設定の対象区分に使います）">
              <select className="input" value={catEditing.kind} onChange={(e) => setCatEditing({ ...catEditing, kind: e.target.value })}>
                {CATEGORY_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="表示順">
              <input className="input num" inputMode="numeric" value={catEditing.sort_order} onChange={(e) => setCatEditing({ ...catEditing, sort_order: e.target.value.replace(/[^\d]/g, "") })} />
            </Field>
            <Toggle checked={catEditing.is_active} onChange={(v) => setCatEditing({ ...catEditing, is_active: v })} label="有効" />
          </div>
        </Modal>
      )}
    </div>
  );
}

function DeactivateProductButton({ id, name, onDone }: { id: number; name: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <>
      <button className="btn btn-ghost mr-auto" onClick={() => setOpen(true)}>
        販売停止
      </button>
      {open && (
        <ConfirmDialog
          title={`「${name}」を販売停止`}
          message="注文画面に表示されなくなります。過去の明細・在庫履歴は残ります。"
          confirmLabel="販売停止にする"
          danger
          busy={busy}
          onCancel={() => setOpen(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api(`/api/store/products/${id}`, { method: "DELETE" });
              setOpen(false);
              onDone();
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <ErrorBox error={err} />
        </ConfirmDialog>
      )}
    </>
  );
}

function ProductFormFields({ form, categories, onChange }: { form: ProductForm; categories: Category[]; onChange: (f: ProductForm) => void }) {
  const set = (patch: Partial<ProductForm>) => onChange({ ...form, ...patch });
  const numOnly = (v: string) => v.replace(/[^\d.]/g, "");
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <Field label="商品名（必須）">
        <input className="input" value={form.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      <Field label="カテゴリ（必須）">
        <select className="input" value={form.category ?? ""} onChange={(e) => set({ category: e.target.value ? Number(e.target.value) : null })}>
          <option value="">選択してください</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="販売価格（円）">
        <input className="input num" inputMode="numeric" value={form.price} onChange={(e) => set({ price: numOnly(e.target.value) })} />
      </Field>
      <Field label="税率（%）">
        <input className="input num" inputMode="decimal" value={form.tax_rate} onChange={(e) => set({ tax_rate: numOnly(e.target.value) })} />
      </Field>
      <Field label="表示順">
        <input className="input num" inputMode="numeric" value={form.sort_order} onChange={(e) => set({ sort_order: numOnly(e.target.value) })} />
      </Field>
      <Field label="原価（1本あたり・任意）" hint="未設定の商品があると利益は概算または算出不可になります。">
        <input className="input num" inputMode="numeric" value={form.cost_price} onChange={(e) => set({ cost_price: numOnly(e.target.value) })} />
      </Field>
      <div className="md:col-span-2">
        <span className="label">在庫管理</span>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          {STOCK_MODES.map((m) => (
            <button key={m.value} type="button" className={`btn btn-sm ${form.stock_mode === m.value ? "btn-primary" : ""}`} onClick={() => set({ stock_mode: m.value })} aria-pressed={form.stock_mode === m.value}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
      {form.stock_mode === "shot" && (
        <>
          <Field label="1本の容量（ml）">
            <input className="input num" inputMode="decimal" value={form.volume_ml} onChange={(e) => set({ volume_ml: numOnly(e.target.value) })} />
          </Field>
          <Field label="1杯量（ml）">
            <input className="input num" inputMode="decimal" value={form.shot_ml} onChange={(e) => set({ shot_ml: numOnly(e.target.value) })} />
          </Field>
        </>
      )}
      {form.stock_mode !== "none" && (
        <>
          <Field label="発注点（本）">
            <input className="input num" inputMode="decimal" value={form.reorder_point} onChange={(e) => set({ reorder_point: numOnly(e.target.value) })} />
          </Field>
          <Field label="最低在庫（本・発注候補の目標）">
            <input className="input num" inputMode="decimal" value={form.min_stock} onChange={(e) => set({ min_stock: numOnly(e.target.value) })} />
          </Field>
        </>
      )}
      <Toggle checked={form.is_bottle_keepable} onChange={(v) => set({ is_bottle_keepable: v })} label="ボトルキープ対象" />
      <Toggle checked={form.is_active} onChange={(v) => set({ is_active: v })} label="販売中" />
      <div className="md:col-span-2">
        <Field label="メモ">
          <input className="input" value={form.note} onChange={(e) => set({ note: e.target.value })} />
        </Field>
      </div>
    </div>
  );
}
