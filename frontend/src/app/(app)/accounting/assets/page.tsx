"use client";

/** 固定資産・減価償却・棚卸（年間決算の材料）。償却率は国税庁の表の値、評価は最終仕入原価法。 */
import { useMemo, useState } from "react";
import { FreeeHandlesNotice, RequireCaps, Tabs, YearPicker } from "@/components/common";
import { DataTable } from "@/components/DataTable";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, Empty, ErrorBox, Field, Modal, PageHeader, Spinner, Stat } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AssetsResponse, DepreciationResponse, FixedAsset, InventoryClosing, InventoryResponse, MethodOption } from "@/lib/types_closing";
import { useLoad } from "@/lib/useLoad";

const KINDS = [
  { key: "tangible", label: "有形固定資産（備品・設備・車両）" },
  { key: "intangible", label: "無形固定資産（ソフトウェア）" },
  { key: "deferred_opening_cost", label: "開業費（繰延資産）" },
  { key: "deferred_other", label: "その他の繰延資産" },
];
const PAID_FROM = [
  { key: "none", label: "仕訳しない（すでに経費・仕訳として記録済み）" },
  { key: "bank", label: "事業用口座から支払った" },
  { key: "cash", label: "レジ以外の現金で支払った" },
  { key: "owner_personal", label: "オーナーの個人のお金で支払った" },
  { key: "card", label: "クレジットカード（後日引落し）" },
];

export default function AssetsPage() {
  const [tab, setTab] = useState("assets");
  const [year, setYear] = useState(new Date().getFullYear());
  return (
    <RequireCaps caps={["accounting.view"]}>
      <PageHeader title="固定資産・棚卸" subtitle="10万円以上の備品・設備、開業費、年末の在庫を登録して、減価償却と期末棚卸を計算します。" actions={<YearPicker value={year} onChange={setYear} />} />
      <FreeeHandlesNotice what="固定資産・減価償却・棚卸" />
      <Tabs tabs={[{ key: "assets", label: "固定資産" }, { key: "depreciation", label: "減価償却（年度別）" }, { key: "inventory", label: "棚卸" }]} value={tab} onChange={setTab} />
      {tab === "assets" && <AssetsTab year={year} />}
      {tab === "depreciation" && <DepreciationTab year={year} />}
      {tab === "inventory" && <InventoryTab year={year} />}
    </RequireCaps>
  );
}

// --- 固定資産 ---------------------------------------------------------------------------------
function AssetsTab({ year }: { year: number }) {
  const { can } = useSession();
  const q = useLoad<AssetsResponse>(`/api/closing/fixed-assets?year=${year}`, [year]);
  const [editing, setEditing] = useState<FixedAsset | "new" | null>(null);
  const [dispose, setDispose] = useState<FixedAsset | null>(null);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const data = q.data!;
  const canEdit = can("yearend.prepare");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {canEdit && (
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            ＋ 資産・開業費を登録
          </button>
        )}
        <span className="text-sm self-center" style={{ color: "var(--color-muted)" }}>
          10万円未満は消耗品費として経費にできます。10万円以上は資産として登録し、毎年少しずつ経費にします（減価償却）。
        </span>
      </div>
      <DataTable
        columns={[
          { key: "name", label: "資産名", render: (a) => <span>{a.name}{a.category ? <span className="text-sm ml-1" style={{ color: "var(--color-muted)" }}>{a.category}</span> : null}</span> },
          { key: "acquired_on", label: "取得日", render: (a) => fmtDate(a.acquired_on) },
          { key: "acquisition_cost", label: "取得価額", align: "right", render: (a) => yen(a.acquisition_cost) },
          { key: "method_label", label: "償却方法", render: (a) => <span>{a.method_label}{a.useful_life_years ? `（${a.useful_life_years}年・率${a.rate}）` : ""}</span> },
          { key: "business_use_percent", label: "事業割合", align: "right", render: (a) => `${Number(a.business_use_percent)}%` },
          { key: "accumulated", label: "償却累計", align: "right", render: (a) => yen(a.accumulated) },
          { key: "book_value", label: "帳簿価額", align: "right", render: (a) => yen(a.book_value) },
          { key: "status_label", label: "状態", render: (a) => <span className={`badge ${a.status === "in_use" ? "badge-ok" : ""}`}>{a.status_label}{a.disposed_on ? ` ${fmtDate(a.disposed_on)}` : ""}</span> },
          {
            key: "ops",
            label: "",
            render: (a) =>
              canEdit ? (
                <span className="flex gap-1">
                  <button className="btn btn-sm" onClick={() => setEditing(a)}>編集</button>
                  {a.status === "in_use" && <button className="btn btn-sm" onClick={() => setDispose(a)}>除却・売却</button>}
                </span>
              ) : null,
          },
        ]}
        rows={data.assets}
        rowKey={(a) => a.id}
        emptyText="登録された固定資産はありません。"
      />
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        償却率の出典: {data.rates.source}（<a className="underline" href={data.rates.source_url} target="_blank" rel="noreferrer">国税庁</a>）
      </p>
      {editing && <AssetModal asset={editing === "new" ? null : editing} methods={data.methods} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); q.reload(); }} />}
      {dispose && <DisposeModal asset={dispose} onClose={() => setDispose(null)} onSaved={() => { setDispose(null); q.reload(); }} />}
    </div>
  );
}

function AssetModal({ asset, methods, onClose, onSaved }: { asset: FixedAsset | null; methods: MethodOption[]; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({
    name: asset?.name ?? "",
    kind: asset?.kind ?? "tangible",
    category: asset?.category ?? "器具備品",
    acquired_on: asset?.acquired_on ?? "",
    acquisition_cost: asset ? String(Math.round(Number(asset.acquisition_cost))) : "",
    business_use_percent: asset ? String(Number(asset.business_use_percent)) : "100",
    depreciation_method: asset?.depreciation_method ?? "straight_line",
    useful_life_years: asset?.useful_life_years ? String(asset.useful_life_years) : "",
    paid_from: asset?.paid_from ?? "none",
    note: asset?.note ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const method = methods.find((m) => m.key === f.depreciation_method);
  const isDeferred = f.kind.startsWith("deferred");
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const body = { ...f, useful_life_years: f.useful_life_years ? Number(f.useful_life_years) : null };
      if (asset) await api(`/api/closing/fixed-assets/${asset.id}`, { method: "PATCH", body });
      else await api("/api/closing/fixed-assets", { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      toast.show({ message: asset ? "資産を更新しました" : "資産を登録しました" });
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={asset ? "固定資産を編集" : "固定資産・開業費を登録"}
      onClose={busy ? undefined : onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>やめる</button>
          <button className="btn btn-primary" onClick={save} disabled={busy} style={{ minWidth: 200 }}>{busy ? "保存中…" : "保存する"}</button>
        </>
      }
    >
      <div className="grid md:grid-cols-2 gap-3">
        <Field label="資産名">
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="例: 業務用冷蔵庫" />
        </Field>
        <Field label="種類">
          <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as FixedAsset["kind"], depreciation_method: e.target.value.startsWith("deferred") ? "voluntary" : f.depreciation_method === "voluntary" ? "straight_line" : f.depreciation_method })}>
            {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
        </Field>
        <Field label="分類（器具備品・建物附属設備・車両運搬具など）">
          <input className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} />
        </Field>
        <Field label={isDeferred ? "開業日" : "取得日（使い始めた日）"}>
          <input type="date" className="input" value={f.acquired_on} onChange={(e) => setF({ ...f, acquired_on: e.target.value })} />
        </Field>
        <Field label="取得価額（税込・円）">
          <input className="input input-lg" inputMode="numeric" value={f.acquisition_cost} onChange={(e) => setF({ ...f, acquisition_cost: e.target.value.replace(/[^\d]/g, "") })} />
        </Field>
        <Field label="事業専用割合（%）" hint="自宅と兼用している車・パソコンなどは事業で使う割合だけ経費になります">
          <input className="input" inputMode="numeric" value={f.business_use_percent} onChange={(e) => setF({ ...f, business_use_percent: e.target.value.replace(/[^\d.]/g, "") })} />
        </Field>
        <Field label="償却方法" hint={method?.help}>
          <select className="input" value={f.depreciation_method} onChange={(e) => setF({ ...f, depreciation_method: e.target.value as FixedAsset["depreciation_method"] })}>
            {methods.map((m) => (
              <option key={m.key} value={m.key} disabled={!m.available}>
                {m.label}{m.available ? "" : "（この年度では使えません）"}
              </option>
            ))}
          </select>
        </Field>
        {f.depreciation_method === "straight_line" && (
          <Field label="耐用年数（年）" hint="国税庁の耐用年数表で確認。例: 冷蔵庫 6年、パソコン 4年、テーブル・椅子 8年（金属製 15年）、看板 3年">
            <input className="input" inputMode="numeric" value={f.useful_life_years} onChange={(e) => setF({ ...f, useful_life_years: e.target.value.replace(/[^\d]/g, "") })} />
          </Field>
        )}
        <Field label="取得時の支払い" hint="すでに経費・現金出金・銀行明細で記録している場合は「仕訳しない」。二重に計上しないため">
          <select className="input" value={f.paid_from} onChange={(e) => setF({ ...f, paid_from: e.target.value })} disabled={!!asset}>
            {PAID_FROM.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="メモ">
          <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </Field>
      </div>
      {err ? <div className="mt-3"><ErrorBox error={err} /></div> : null}
    </Modal>
  );
}

function DisposeModal({ asset, onClose, onSaved }: { asset: FixedAsset; onClose: () => void; onSaved: () => void }) {
  const [kind, setKind] = useState<"disposed" | "sold">("disposed");
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const toast = useToast();
  return (
    <ConfirmDialog
      title={`${asset.name} を除却・売却`}
      confirmLabel="登録する"
      busy={busy}
      onCancel={onClose}
      onConfirm={async () => {
        setBusy(true);
        setErr(null);
        try {
          const r = await api<{ notice: string }>(`/api/closing/fixed-assets/${asset.id}/dispose`, { method: "POST", body: { kind, disposed_on: date, disposal_amount: amount || null }, idempotencyKey: newIdempotencyKey() });
          toast.show({ message: r.notice, tone: "info", durationMs: 8000 });
          onSaved();
        } catch (e) {
          setErr(e);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <button className={`btn ${kind === "disposed" ? "btn-primary" : ""}`} onClick={() => setKind("disposed")}>除却（捨てた・壊れた）</button>
          <button className={`btn ${kind === "sold" ? "btn-primary" : ""}`} onClick={() => setKind("sold")}>売却</button>
        </div>
        <Field label="日付"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        {kind === "sold" && <Field label="売却額（円）"><input className="input" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d]/g, ""))} /></Field>}
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>その年の償却は使った月までで計算します。帳簿価額が残る場合の除却損・売却益は自動では仕訳しません（税理士に確認）。</p>
        {err ? <ErrorBox error={err} /> : null}
      </div>
    </ConfirmDialog>
  );
}

// --- 減価償却 ---------------------------------------------------------------------------------
function DepreciationTab({ year }: { year: number }) {
  const { can } = useSession();
  const toast = useToast();
  const q = useLoad<DepreciationResponse>(`/api/closing/depreciation?year=${year}`, [year]);
  const assets = useLoad<AssetsResponse>(`/api/closing/fixed-assets?year=${year}`, [year]);
  const [voluntary, setVoluntary] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const voluntaryAssets = useMemo(() => (assets.data?.assets ?? []).filter((a) => a.depreciation_method === "voluntary" && a.status === "in_use"), [assets.data]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const data = q.data!;
  async function compute() {
    setBusy(true);
    setErr(null);
    try {
      const vol: Record<string, string> = {};
      for (const [k, v] of Object.entries(voluntary)) if (v !== "") vol[k] = v;
      const r = await api<DepreciationResponse>("/api/closing/depreciation/compute", { method: "POST", body: { year, voluntary: vol }, idempotencyKey: newIdempotencyKey() });
      toast.show({ message: `${year}年分の減価償却を計算しました（必要経費 ${yen(r.total_business)}）` });
      q.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <Stat label={`${year}年分の減価償却費（必要経費）`} value={yen(data.total_business)} />
        <Stat label="対象資産" value={`${data.entries.length} 件`} />
        <Stat label="年度の状態" value={data.locked ? "締め済み" : "進行中"} accent={data.locked ? "warn" : "ok"} />
      </div>
      {voluntaryAssets.length > 0 && (
        <div className="card">
          <h2 className="font-bold mb-2">開業費など（任意償却）の今年の償却額</h2>
          <p className="text-sm mb-2" style={{ color: "var(--color-muted)" }}>残額の範囲で、今年経費にする額を決めます。利益の出た年に多く償却するのが一般的です。空欄なら前回の額（初回は 0）。</p>
          <div className="grid md:grid-cols-2 gap-2">
            {voluntaryAssets.map((a) => (
              <Field key={a.id} label={`${a.name}（残額 ${yen(a.book_value)}）`}>
                <input className="input" inputMode="numeric" value={voluntary[a.id] ?? ""} onChange={(e) => setVoluntary({ ...voluntary, [a.id]: e.target.value.replace(/[^\d]/g, "") })} placeholder="今年の償却額" />
              </Field>
            ))}
          </div>
        </div>
      )}
      {can("yearend.prepare") && !data.locked && (
        <button className="btn btn-primary btn-lg" onClick={compute} disabled={busy}>
          {busy ? "計算中…" : `${year}年分の減価償却を計算する（計算し直しても二重になりません）`}
        </button>
      )}
      {err ? <ErrorBox error={err} /> : null}
      <DataTable
        columns={[
          { key: "asset_name", label: "資産名" },
          { key: "method_label", label: "方法" },
          { key: "acquisition_cost", label: "取得価額", align: "right", render: (e) => yen(e.acquisition_cost) },
          { key: "rate", label: "率", render: (e) => e.rate ?? "" },
          { key: "months_in_service", label: "月数", align: "right" },
          { key: "amount_total", label: "償却費", align: "right", render: (e) => yen(e.amount_total) },
          { key: "business_use_percent", label: "事業%", align: "right", render: (e) => `${Number(e.business_use_percent)}%` },
          { key: "amount_business", label: "必要経費", align: "right", render: (e) => yen(e.amount_business) },
          { key: "book_value_end", label: "期末帳簿価額", align: "right", render: (e) => yen(e.book_value_end) },
          { key: "formula", label: "計算", render: (e) => <span className="text-sm" style={{ color: "var(--color-muted)" }}>{String(e.calc_basis.formula ?? e.calc_basis.note ?? "")}{e.calc_basis.capped ? `（${String(e.calc_basis.capped)}）` : ""}</span> },
        ]}
        rows={data.entries}
        rowKey={(e) => e.id}
        emptyText="まだ計算していません。「計算する」を押すと、登録した資産ごとに償却費と仕訳を作ります。"
      />
    </div>
  );
}

// --- 棚卸 -----------------------------------------------------------------------------------
function InventoryTab({ year }: { year: number }) {
  const { can, me, store } = useSession();
  const toast = useToast();
  const q = useLoad<InventoryResponse>(`/api/closing/inventory?year=${year}`, [year]);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<InventoryClosing | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<InventoryClosing | null>(null);
  const [busy, setBusy] = useState(false);
  const stores = me?.stores.filter((s) => s.company.id === store?.company.id) ?? [];
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const data = q.data!;
  const canEdit = can("yearend.prepare");
  async function act(c: InventoryClosing, action: "confirm" | "unconfirm", reason?: string) {
    setBusy(true);
    try {
      await api(`/api/closing/inventory/${c.id}/${action}`, { method: "POST", body: { reason }, idempotencyKey: newIdempotencyKey() });
      toast.show({ message: action === "confirm" ? "棚卸を確定し、決算整理仕訳を作りました" : "確定を取り消しました" });
      q.reload();
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
      setConfirmTarget(null);
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        年末に残っている商品（ボトルなど）の仕入値の合計が「期末商品棚卸高」です。評価は最終仕入原価法（最後に仕入れた単価）。翌年の期首は前年の期末から作れます。
      </p>
      {canEdit && (
        <button className="btn btn-primary" onClick={() => setCreating(true)}>＋ 棚卸を作る</button>
      )}
      <DataTable
        columns={[
          { key: "kind_label", label: "区分" },
          { key: "store_name", label: "店舗" },
          { key: "as_of", label: "日付", render: (c) => fmtDate(c.as_of) },
          { key: "method_label", label: "評価方法" },
          { key: "total_amount", label: "金額", align: "right", render: (c) => yen(c.total_amount) },
          { key: "unpriced_count", label: "", render: (c) => (c.unpriced_count ? <span className="badge badge-warn">単価なし {c.unpriced_count} 件</span> : null) },
          { key: "status", label: "状態", render: (c) => <span className={`badge ${c.status === "confirmed" ? "badge-ok" : "badge-warn"}`}>{c.status === "confirmed" ? "確定" : "下書き"}</span> },
          {
            key: "ops",
            label: "",
            render: (c) => (
              <span className="flex gap-1">
                <button className="btn btn-sm" onClick={async () => setDetail(await api<InventoryClosing>(`/api/closing/inventory/${c.id}`))}>内訳</button>
                {canEdit && c.status === "draft" && <button className="btn btn-sm btn-ok" onClick={() => setConfirmTarget(c)}>確定</button>}
                {canEdit && c.status === "confirmed" && (
                  <button className="btn btn-sm" onClick={() => { const r = window.prompt("確定を取り消す理由"); if (r) act(c, "unconfirm", r); }}>確定を取消</button>
                )}
              </span>
            ),
          },
        ]}
        rows={data.closings}
        rowKey={(c) => c.id}
        emptyText="この年度の棚卸はありません。"
      />
      {creating && (
        <NewInventoryModal year={year} stores={stores} stockTakes={data.stock_takes} onClose={() => setCreating(false)} onSaved={() => { setCreating(false); q.reload(); }} />
      )}
      {detail && (
        <Modal title={`${detail.kind_label}棚卸の内訳（${detail.store_name}）`} onClose={() => setDetail(null)} wide>
          {detail.lines?.length ? (
            <DataTable
              columns={[
                { key: "name", label: "商品" },
                { key: "quantity", label: "数量（本）", align: "right", render: (l) => Number(l.quantity).toLocaleString("ja-JP") },
                { key: "unit_cost", label: "単価", align: "right", render: (l) => yen(l.unit_cost) },
                { key: "amount", label: "金額", align: "right", render: (l) => yen(l.amount) },
                { key: "cost_source", label: "単価の出所", render: (l) => ({ supplier_product: "仕入先の単価", product_cost: "商品の原価", manual: "手入力", none: "なし（要確認）" }[l.cost_source] ?? l.cost_source) },
              ]}
              rows={detail.lines}
              rowKey={(l) => l.id}
            />
          ) : (
            <Empty>内訳はありません</Empty>
          )}
        </Modal>
      )}
      {confirmTarget && (
        <ConfirmDialog
          title="棚卸を確定する"
          message={`${confirmTarget.store_name} の${confirmTarget.kind_label}棚卸 ${yen(confirmTarget.total_amount)} を確定し、決算整理仕訳を作ります。`}
          confirmLabel="確定する"
          busy={busy}
          onCancel={() => setConfirmTarget(null)}
          onConfirm={() => act(confirmTarget, "confirm")}
        />
      )}
    </div>
  );
}

function NewInventoryModal({ year, stores, stockTakes, onClose, onSaved }: { year: number; stores: { id: number; name: string }[]; stockTakes: InventoryResponse["stock_takes"]; onClose: () => void; onSaved: () => void }) {
  const [storeId, setStoreId] = useState<number>(stores[0]?.id ?? 0);
  const [mode, setMode] = useState<"stock_take" | "manual" | "from_previous">("stock_take");
  const [kind, setKind] = useState<"closing" | "opening">("closing");
  const [stockTake, setStockTake] = useState<number | "">("");
  const [lines, setLines] = useState([{ name: "", quantity: "", unit_cost: "" }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const takes = stockTakes.filter((t) => t.store === storeId);
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { year, store: storeId, kind };
      if (mode === "from_previous") body.from_previous = true;
      else if (mode === "stock_take") body.stock_take = stockTake;
      else body.lines = lines.filter((l) => l.name);
      await api("/api/closing/inventory", { method: "POST", body, idempotencyKey: newIdempotencyKey() });
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="棚卸を作る" onClose={busy ? undefined : onClose} wide footer={<><button className="btn" onClick={onClose} disabled={busy}>やめる</button><button className="btn btn-primary" onClick={save} disabled={busy || (mode === "stock_take" && !stockTake)}>{busy ? "作成中…" : "下書きを作る"}</button></>}>
      <div className="space-y-3">
        <div className="grid md:grid-cols-2 gap-3">
          <Field label="店舗">
            <select className="input" value={storeId} onChange={(e) => setStoreId(Number(e.target.value))}>
              {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="区分">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value as "closing" | "opening")} disabled={mode === "from_previous"}>
              <option value="closing">期末（{year}年12月31日）</option>
              <option value="opening">期首（{year}年1月1日）</option>
            </select>
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <button className={`btn ${mode === "stock_take" ? "btn-primary" : ""}`} onClick={() => setMode("stock_take")}>棚卸し結果から</button>
          <button className={`btn ${mode === "manual" ? "btn-primary" : ""}`} onClick={() => setMode("manual")}>手で入力</button>
          <button className={`btn ${mode === "from_previous" ? "btn-primary" : ""}`} onClick={() => { setMode("from_previous"); setKind("opening"); }}>前年の期末から期首を作る</button>
        </div>
        {mode === "stock_take" && (
          <Field label="棚卸し結果（商品・在庫・棚卸し画面で数えたもの）">
            <select className="input" value={stockTake} onChange={(e) => setStockTake(e.target.value ? Number(e.target.value) : "")}>
              <option value="">選んでください</option>
              {takes.map((t) => <option key={t.id} value={t.id}>{fmtDate(t.taken_at)}（{t.lines} 商品・{t.status === "applied" ? "反映済み" : "下書き"}）</option>)}
            </select>
          </Field>
        )}
        {mode === "manual" && (
          <div className="space-y-2">
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-3 gap-2">
                <input className="input" placeholder="商品名" value={l.name} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <input className="input" placeholder="数量（本）" inputMode="decimal" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
                <input className="input" placeholder="仕入単価（円）" inputMode="decimal" value={l.unit_cost} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, unit_cost: e.target.value } : x)))} />
              </div>
            ))}
            <button className="btn btn-sm" onClick={() => setLines([...lines, { name: "", quantity: "", unit_cost: "" }])}>＋ 行を追加</button>
          </div>
        )}
        {mode === "from_previous" && <p className="text-sm" style={{ color: "var(--color-muted)" }}>{year - 1}年の確定した期末棚卸と同じ内容で、{year}年の期首棚卸の下書きを作ります。</p>}
        {err ? <ErrorBox error={err} /> : null}
      </div>
    </Modal>
  );
}
