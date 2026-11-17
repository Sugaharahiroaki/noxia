"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { RequireCaps } from "@/components/common";
import { useToast } from "@/components/Toast";
import { Empty, ErrorBox, Spinner } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useTerms } from "@/lib/terms";
import type { Category, Course, StoreSettings } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

/**
 * POS 設定（タブレット用）: 左＝設定項目｜中央＝一覧・検索・追加｜右＝詳細。
 * 項目が 1 つだけの設定（料金・会計アラート・料金計算）は、中央と右を合わせた広いフォームにする。
 * すべて既存の API（store/tables・courses・categories・products・tags、store/settings）につなぐ。
 * 読むのは settings.view、書くのは catalog.edit（マスタ）／settings.edit＋パスワードの再確認（店舗設定）。
 * プリンター・レシート・領収書・キッチン印刷・注文チャイム・名称変更は出さない（UNIFIED_PLAN 6 章 Q11）。
 */

type Row = Record<string, unknown> & { id?: number };
type Option = { value: string; label: string };
type FieldSpec = {
  key: string;
  label: string;
  type: "text" | "int" | "money" | "moneyOrNull" | "minutesOrUnlimited" | "select" | "toggle" | "color" | "extOptions" | "readonly";
  options?: Option[] | ((ctx: Ctx) => Option[]);
  hint?: string;
  /** 空欄のときの表示（既定: moneyOrNull は「未設定」） */
  placeholder?: string;
  required?: boolean;
  format?: (row: Row) => string;
};
type Ctx = { categories: Category[] };
type MasterSection = {
  kind: "master";
  key: string;
  label: string;
  endpoint: string;
  itemLabel: string;
  title: (r: Row) => string;
  sub: (r: Row, ctx: Ctx) => string;
  swatch?: (r: Row) => string;
  fields: FieldSpec[];
  defaults: Row;
  copy?: boolean;
};
type FormSection = { kind: "form"; key: string; label: string; intro: string; fields: FieldSpec[] };
type DesignationSection = { kind: "designations"; key: string; label: string };
type Section = MasterSection | FormSection | DesignationSection;

const COLORS = ["", "#d9534f", "#e8801a", "#c9a227", "#1a8a52", "#1f6fd0", "#6f42c1", "#8a6d3b", "#555555"];
const ROUND: Option[] = [
  { value: "floor", label: "切り捨て" },
  { value: "ceil", label: "切り上げ" },
  { value: "half_up", label: "四捨五入" },
];
const CATEGORY_KINDS: Option[] = [
  { value: "drink", label: "ドリンク" },
  { value: "cast_drink", label: "キャストドリンク" },
  { value: "bottle", label: "ボトル" },
  { value: "shot", label: "ショット" },
  { value: "food", label: "フード" },
  { value: "other", label: "その他" },
];
const STOCK_LABEL: Record<string, string> = { none: "在庫を管理しない", bottle: "本数で管理", shot: "ショット販売（容量から計算）" };
const active = (r: Row) => (r.is_active === false ? "（非表示）" : "");

const SECTIONS: Section[] = [
  {
    kind: "master",
    key: "tables",
    label: "テーブル",
    endpoint: "/api/store/tables",
    itemLabel: "テーブル",
    title: (r) => `${r.name}${active(r)}`,
    sub: (r) => [r.kind || "区分なし", `定員 ${r.capacity}`, `並び順 ${r.sort_order}`].join(" ・ "),
    swatch: (r) => String(r.color ?? ""),
    defaults: { name: "", capacity: 4, sort_order: 0, is_active: true, color: "", kind: "" },
    fields: [
      { key: "name", label: "テーブル名", type: "text", required: true },
      { key: "is_active", label: "ホールに表示する", type: "toggle" },
      { key: "color", label: "色（卓カードの上の帯）", type: "color" },
      { key: "kind", label: "区分（営業枠。例: BOX・カウンター）", type: "text", hint: "同じ区分の卓がホールでまとまって並びます。" },
      { key: "sort_order", label: "並び順（小さい順）", type: "int" },
      { key: "capacity", label: "定員", type: "int" },
    ],
  },
  {
    kind: "master",
    key: "courses",
    label: "セット構成",
    endpoint: "/api/store/courses",
    itemLabel: "セット",
    copy: true,
    title: (r) => `${r.name}${active(r)}`,
    sub: (r) => [r.kind || "区分なし", r.duration_minutes ? `${r.duration_minutes}分` : "時間無制限", yen(String(r.price ?? ""))].join(" ・ "),
    defaults: { name: "", price: "0", duration_minutes: 60, extension_price: "0", extension_minutes: 60, kind: "", is_active: true, sort_order: 0, extension_options: [] },
    fields: [
      { key: "name", label: "名称", type: "text", required: true },
      { key: "kind", label: "区分（入店時のセット構成の入口）", type: "text", hint: "区分が 2 種類以上あると、入店のときに最初に区分を選びます。" },
      { key: "duration_minutes", label: "時間", type: "minutesOrUnlimited" },
      { key: "price", label: "料金（円）", type: "money", required: true },
      { key: "extension_options", label: "延長の候補", type: "extOptions", hint: "候補が無いときは、下の「延長（候補なしのとき）」の 1 種類だけになります。" },
      { key: "extension_minutes", label: "延長（候補なしのとき）の分数", type: "int" },
      { key: "extension_price", label: "延長（候補なしのとき）の料金（円）", type: "money" },
      { key: "is_active", label: "入店の候補に表示する", type: "toggle" },
      { key: "sort_order", label: "並び順（小さい順）", type: "int" },
    ],
  },
  {
    kind: "master",
    key: "categories",
    label: "カテゴリー",
    endpoint: "/api/store/categories",
    itemLabel: "カテゴリー",
    title: (r) => `${r.name}${active(r)}`,
    sub: (r) => [CATEGORY_KINDS.find((k) => k.value === r.kind)?.label ?? String(r.kind), `並び順 ${r.sort_order}`].join(" ・ "),
    defaults: { name: "", kind: "drink", sort_order: 0, is_active: true },
    fields: [
      { key: "name", label: "カテゴリー名", type: "text", required: true },
      { key: "kind", label: "種別（バックの条件・集計に使う）", type: "select", options: CATEGORY_KINDS },
      { key: "is_active", label: "注文画面に表示する", type: "toggle" },
      { key: "sort_order", label: "並び順（小さい順）", type: "int" },
    ],
  },
  {
    kind: "master",
    key: "products",
    label: "商品",
    endpoint: "/api/store/products",
    itemLabel: "商品",
    title: (r) => `${r.name}${r.short_name ? `（${r.short_name}）` : ""}${active(r)}`,
    sub: (r) => [r.category_name, yen(String(r.price ?? "")), r.is_bottle_keepable ? "キープ可" : "", r.stock_mode && r.stock_mode !== "none" ? "在庫あり" : ""].filter(Boolean).join(" ・ "),
    defaults: { name: "", short_name: "", category: null, price: "0", is_active: true, sort_order: 0, is_bottle_keepable: false },
    fields: [
      { key: "name", label: "商品名（伝票に残る名前）", type: "text", required: true },
      { key: "short_name", label: "略称（注文画面に出す短い名前。空なら商品名）", type: "text" },
      { key: "category", label: "カテゴリー", type: "select", required: true, options: (ctx) => ctx.categories.filter((c) => c.is_active).map((c) => ({ value: String(c.id), label: c.name })) },
      { key: "price", label: "販売価格（円）", type: "money", required: true },
      { key: "is_bottle_keepable", label: "ボトルキープの対象", type: "toggle" },
      { key: "stock_mode", label: "在庫", type: "readonly", format: (r) => `${STOCK_LABEL[String(r.stock_mode ?? "none")] ?? ""}（変更は「商品・在庫・棚卸し」で行います）` },
      { key: "is_active", label: "注文画面に表示する（販売中）", type: "toggle" },
      { key: "sort_order", label: "並び順（小さい順）", type: "int" },
    ],
  },
  {
    kind: "master",
    key: "tags",
    label: "タグ",
    endpoint: "/api/store/tags",
    itemLabel: "タグ",
    title: (r) => `${r.name}${active(r)}`,
    sub: (r) => [r.show_on_floor ? "ホールに表示" : "ホールには出さない", `並び順 ${r.sort_order}`].join(" ・ "),
    swatch: (r) => String(r.color ?? ""),
    defaults: { name: "", color: "", show_on_floor: true, sort_order: 0, is_active: true },
    fields: [
      { key: "name", label: "タグ名", type: "text", required: true },
      { key: "color", label: "色", type: "color" },
      { key: "show_on_floor", label: "ホールの卓カードに表示する", type: "toggle" },
      { key: "is_active", label: "タグ選択の候補に表示する", type: "toggle" },
      { key: "sort_order", label: "並び順（小さい順）", type: "int" },
    ],
  },
  { kind: "designations", key: "fees", label: "指名・同伴（種類・名前・料金）" },
  {
    kind: "form",
    key: "alarm",
    label: "会計アラート",
    intro: "セットの終了が近づいたときのお知らせ（画面上部の帯と卓カードの色）を、いつ出すかを選びます。",
    fields: [
      {
        key: "alarm_mode",
        label: "お知らせを出すタイミング",
        type: "select",
        options: [
          { value: "10", label: "10 分前のみ" },
          { value: "5", label: "5 分前のみ" },
          { value: "both", label: "10 分前と 5 分前の両方" },
        ],
      },
    ],
  },
  {
    kind: "form",
    key: "pricing",
    label: "料金計算（サービス料・税・丸め）",
    intro: "ここを変えると、次の会計の見積から新しい計算になります。確定済みの会計は変わりません。営業中の伝票の金額は、会計前なら新しい設定で計算し直されます。",
    fields: [
      { key: "service_charge_enabled", label: "サービス料をかける", type: "toggle" },
      {
        key: "service_charge_rate",
        label: "サービス料率（%・標準）",
        type: "money",
        hint: "支払方法を選ぶ前の金額（ホール・卓詳細）と、キャストのバックの計算に使います。下の支払方法別の料率が空欄の支払方法もこの料率です。",
      },
      { key: "service_charge_rate_cash", label: "現金で払うときのサービス料率（%）", type: "moneyOrNull", placeholder: "空欄 = 標準の料率" },
      {
        key: "service_charge_rate_card",
        label: "カードで払うときのサービス料率（%）",
        type: "moneyOrNull",
        placeholder: "空欄 = 標準の料率",
        hint: "現金＋カードで払うときも、会計全体にこの料率をかけます。決済端末に送る金額もこの料率です。",
      },
      { key: "service_charge_rate_receivable", label: "ツケのときのサービス料率（%）", type: "moneyOrNull", placeholder: "空欄 = 標準の料率" },
      {
        key: "service_charge_target",
        label: "サービス料の対象",
        type: "select",
        options: [
          { value: "all", label: "全明細" },
          { value: "course_only", label: "セット・延長・コース変更のみ" },
          { value: "products_only", label: "商品のみ" },
        ],
      },
      {
        key: "service_charge_order",
        label: "サービス料と消費税の順序（価格が税抜のとき）",
        type: "select",
        options: [
          { value: "before_tax", label: "税抜金額にかけ、サービス料にも消費税をかける" },
          { value: "after_tax", label: "税込金額にかけ、サービス料には消費税をかけない" },
        ],
      },
      { key: "service_charge_after_discount", label: "値引き後の金額にサービス料をかける", type: "toggle" },
      { key: "service_charge_rounding", label: "サービス料の端数", type: "select", options: ROUND },
      { key: "prices_include_tax", label: "価格は税込（内税）", type: "toggle" },
      { key: "default_tax_rate", label: "標準税率（%）", type: "money" },
      { key: "tax_rounding", label: "消費税の端数", type: "select", options: ROUND },
      { key: "total_rounding_unit", label: "合計の丸め単位（円。1 = 丸めない）", type: "int" },
      { key: "total_rounding_mode", label: "合計の丸め方", type: "select", options: ROUND },
    ],
  },
];

export default function PosSettingsPage() {
  return (
    <RequireCaps caps={["settings.view"]}>
      <Inner />
    </RequireCaps>
  );
}

function Inner() {
  const [sectionKey, setSectionKey] = useState("tables");
  const section = SECTIONS.find((s) => s.key === sectionKey)!;
  const categories = useLoad<Category[]>("/api/store/categories");
  const ctx: Ctx = { categories: categories.data ?? [] };
  const pane = { background: "var(--color-panel)", border: "1.5px solid var(--color-border)", borderRadius: 14 } as const;

  return (
    <div className="flex flex-col gap-2" style={{ height: "calc(100dvh - 64px - 34px)", minHeight: 420 }}>
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">POS 設定</h1>
        <span className="text-sm" style={{ color: "var(--color-muted)" }}>
          変更は次の新しい伝票・注文から反映されます。確定済みの明細の単価やバックは変わりません。
        </span>
        <Link href="/floor" className="btn btn-sm ml-auto">
          ← ホールへ
        </Link>
      </div>
      <div className="grid gap-2 flex-1 min-h-0" style={{ gridTemplateColumns: "210px minmax(0, 1fr)" }}>
        <nav className="flex flex-col min-h-0" style={pane} aria-label="設定項目">
          <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]">設定項目</h2>
          <div className="flex-1 overflow-y-auto p-1.5 space-y-1.5">
            {SECTIONS.map((s) => (
              <button key={s.key} className={`w-full rounded-xl px-2 text-left font-bold ${s.key === sectionKey ? "pos-selected" : ""}`} style={{ minHeight: 52, ...(s.key === sectionKey ? {} : { background: "var(--color-panel2)", border: "1px solid var(--color-border)" }) }} onClick={() => setSectionKey(s.key)} aria-current={s.key === sectionKey ? "page" : undefined}>
                {s.label}
              </button>
            ))}
            <div className="pt-2 text-xs px-1" style={{ color: "var(--color-muted)" }}>
              そのほかの設定（PC 向け）
            </div>
            <Link href="/settings/store" className="btn btn-sm w-full">
              店舗設定・権限
            </Link>
            <Link href="/settings/pricing" className="btn btn-sm w-full">
              時給・バック・料金
            </Link>
            <Link href="/inventory" className="btn btn-sm w-full">
              商品・在庫・棚卸し
            </Link>
          </div>
        </nav>
        {section.kind === "master" ? (
          <MasterEditor key={section.key} section={section} ctx={ctx} onCategoriesChanged={() => categories.reload()} pane={pane} />
        ) : section.kind === "designations" ? (
          <DesignationsEditor key={section.key} label={section.label} pane={pane} />
        ) : (
          <SettingsForm key={section.key} section={section} pane={pane} />
        )}
      </div>
    </div>
  );
}

/** 中央: 一覧・検索・追加 ／ 右: 詳細 */
function MasterEditor({ section, ctx, onCategoriesChanged, pane }: { section: MasterSection; ctx: Ctx; onCategoriesChanged: () => void; pane: React.CSSProperties }) {
  const { can } = useSession();
  const toast = useToast();
  const canEdit = can("catalog.edit");
  const list = useLoad<Row[]>(section.endpoint);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rows = useMemo(() => (list.data ?? []).filter((r) => q.trim() === "" || section.title(r).includes(q.trim()) || section.sub(r, ctx).includes(q.trim())), [list.data, q, section, ctx]);

  function pick(r: Row | "new") {
    setError(null);
    if (r === "new") {
      setSelected("new");
      setForm({ ...section.defaults });
    } else {
      setSelected(r.id!);
      setForm({ ...r });
    }
  }

  async function save() {
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body: Row = {};
      for (const f of section.fields) {
        if (f.type === "readonly") continue;
        let v = form[f.key];
        if (f.type === "int") v = v === "" || v === null || v === undefined ? 0 : Number(v);
        if (f.type === "select" && f.key === "category") v = v ? Number(v) : null;
        if (f.type === "extOptions") v = ((v as { minutes: unknown; price: unknown }[]) ?? []).filter((o) => String(o.minutes) !== "").map((o, i) => ({ minutes: Number(o.minutes), price: String(o.price || "0"), sort_order: i }));
        body[f.key] = v;
      }
      const saved = selected === "new" ? await api<Row>(section.endpoint, { method: "POST", body }) : await api<Row>(`${section.endpoint}/${selected}`, { method: "PATCH", body });
      await list.reload();
      if (section.key === "categories") onCategoriesChanged();
      setSelected(saved.id!);
      setForm({ ...saved });
      toast.show({ message: `${section.itemLabel}「${section.title(saved)}」を保存しました` });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (typeof selected !== "number") return;
    setBusy(true);
    setError(null);
    try {
      const dup = await api<Row>(`${section.endpoint}/${selected}/copy`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: {} });
      await list.reload();
      setSelected(dup.id!);
      setForm({ ...dup });
      toast.show({ message: `「${dup.name}」を作りました。名称や料金を直して「決定」を押してください。` });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const missing = form ? section.fields.filter((f) => f.required && (form[f.key] === "" || form[f.key] === null || form[f.key] === undefined)).map((f) => f.label) : [];

  return (
    <div className="grid gap-2 min-h-0" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)" }}>
      <section className="flex flex-col min-h-0" style={pane} aria-label={`${section.label}の一覧`}>
        <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px] flex justify-between">
          <span>{section.label}</span>
          <span className="num">{list.data?.length ?? 0}件</span>
        </h2>
        <div className="p-1.5 flex gap-1.5">
          <input className="input" style={{ minHeight: 46 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="検索" aria-label={`${section.label}を検索`} />
          {canEdit && (
            <button className="btn btn-plus whitespace-nowrap" style={{ minHeight: 46 }} onClick={() => pick("new")}>
              ＋ 追加
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-1.5 pt-0 space-y-1.5">
          <ErrorBox error={list.error} onRetry={() => list.reload(false)} />
          {list.loading && <Spinner />}
          {!list.loading && rows.length === 0 && <Empty>{q ? "見つかりません。" : `${section.label}がまだありません。`}</Empty>}
          {rows.map((r) => {
            const on = selected === r.id;
            return (
              <button key={r.id} className={`w-full rounded-xl px-2 py-1 text-left flex items-center gap-2 ${on ? "pos-selected" : ""}`} style={{ minHeight: 52, ...(on ? {} : { background: "var(--color-panel2)", border: "1px solid var(--color-border)", opacity: r.is_active === false ? 0.6 : 1 }) }} onClick={() => pick(r)} aria-pressed={on}>
                {section.swatch && <span className="inline-block rounded-full shrink-0" style={{ width: 18, height: 18, background: section.swatch(r) || "transparent", border: "1px solid rgba(128,128,128,0.6)" }} aria-hidden />}
                <span className="min-w-0">
                  <span className="block font-bold leading-tight truncate">{section.title(r)}</span>
                  <span className="block text-xs truncate" style={{ opacity: 0.8 }}>
                    {section.sub(r, ctx)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="flex flex-col min-h-0" style={pane} aria-label="詳細">
        <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]">{form ? (selected === "new" ? `${section.itemLabel}を追加` : `${section.itemLabel}の詳細`) : "詳細"}</h2>
        {!form ? (
          <Empty>左の一覧から選ぶか、「＋ 追加」を押してください。</Empty>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto p-2 space-y-2">
              {!canEdit && <p className="badge badge-warn">見るだけです（変更には商品・卓の編集権限が必要です）</p>}
              {section.fields.map((f) => (
                <FieldInput key={f.key} spec={f} value={form[f.key]} row={form} ctx={ctx} disabled={!canEdit || busy} onChange={(v) => setForm((x) => ({ ...(x ?? {}), [f.key]: v }))} />
              ))}
            </div>
            <div className="p-2 border-t space-y-1" style={{ borderColor: "var(--color-border)" }}>
              {error && (
                <p className="font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                  {error}
                </p>
              )}
              {canEdit && (
                <div className="flex gap-2">
                  {section.copy && typeof selected === "number" && (
                    <button className="btn" onClick={copy} disabled={busy}>
                      コピー
                    </button>
                  )}
                  <button className="btn btn-lg btn-primary flex-1" onClick={save} disabled={busy || missing.length > 0}>
                    {busy ? "保存中…" : missing.length ? `${missing[0]} を入れてください` : "決定"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

const DESIGNATION_ROWS = [
  { key: "nomination", enabled: "nomination_enabled", fee: "nomination_fee", hint: "来店前から決まっている指名（例: 呼び）" },
  { key: "jounai", enabled: "jounai_enabled", fee: "jounai_fee", hint: "来店後に席で受けた指名" },
  { key: "douhan", enabled: "douhan_enabled", fee: "douhan_fee", hint: "キャストと一緒に来店" },
] as const;
type DesignationDraft = { enabled: boolean; name: string; fee: string };

/**
 * 指名・同伴: 使う種類（オンオフ）・画面に出す名前（名称変更）・お客様に請求する料金をまとめて設定する。
 * 名前は「名称変更」と同じもの（店舗の TermOverride）。料金の空欄は未設定（登録できない）、0 は料金なし。
 */
function DesignationsEditor({ label, pane }: { label: string; pane: React.CSSProperties }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const terms = useTerms();
  const canEdit = can("settings.edit");
  const st = useLoad<StoreSettings>("/api/store/settings");
  const [draft, setDraft] = useState<Record<string, DesignationDraft> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const termOf = (key: string) => terms.rows.find((r) => r.key === key);

  useEffect(() => {
    if (!st.data || terms.rows.length === 0) return;
    const d = st.data;
    setDraft(
      Object.fromEntries(
        DESIGNATION_ROWS.map((r) => [
          r.key,
          { enabled: d[r.enabled], name: termOf(r.key)?.overridden ? termOf(r.key)!.label : "", fee: d[r.fee] === null || d[r.fee] === undefined ? "" : String(Math.round(Number(d[r.fee]))) },
        ]),
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.data, terms.rows]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {};
      for (const r of DESIGNATION_ROWS) {
        body[r.enabled] = draft[r.key].enabled;
        body[r.fee] = draft[r.key].fee === "" ? null : draft[r.key].fee;
      }
      // 名前（名称変更。空欄 = 初期値に戻す。ほかの用語の変更はそのまま）を先に保存する。名前で断られたら料金・オンオフも保存しない
      const labels: Record<string, string> = Object.fromEntries(terms.rows.filter((r) => r.overridden).map((r) => [r.key, r.label]));
      for (const r of DESIGNATION_ROWS) labels[r.key] = draft[r.key].name.trim();
      await withReauth(() => api("/api/store/terms", { method: "PUT", body: { labels } }));
      const d = await withReauth(() => api<StoreSettings>("/api/store/settings", { method: "PUT", body }));
      await terms.reload();
      st.setData(d);
      toast.show({ message: "指名・同伴の設定を保存しました" });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const set = (key: string, patch: Partial<DesignationDraft>) => setDraft((x) => (x ? { ...x, [key]: { ...x[key], ...patch } } : x));
  return (
    <section className="flex flex-col min-h-0" style={pane} aria-label={label}>
      <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]">{label}</h2>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <p style={{ color: "var(--color-muted)" }}>
          使わない種類は入店・卓の画面に出しません。名前を変えると、POS と集計の画面がその名前になります（例: 本指名 → 呼び）。
          料金はお客様に請求する金額で、空欄は「未設定」（登録できません）、0 は料金なし。キャストへのバック（呼び売上の何%など）は「時給・バック・料金」で設定します。
        </p>
        <ErrorBox error={st.error} onRetry={() => st.reload(false)} />
        {(!draft || st.loading) && <Spinner />}
        {!canEdit && <p className="badge badge-warn">見るだけです（変更には店舗設定の編集権限が必要です）</p>}
        {draft &&
          DESIGNATION_ROWS.map((r) => {
            const t = termOf(r.key);
            const d = draft[r.key];
            const shown = d.name.trim() || t?.default || r.key;
            return (
              <div key={r.key} className="card-2 space-y-2" role="group" aria-label={`${t?.default ?? r.key} の設定`} style={{ opacity: d.enabled ? 1 : 0.7 }}>
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-lg font-bold" style={{ minWidth: 96 }}>
                    {t?.default}
                  </span>
                  <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                    {r.hint}
                  </span>
                </div>
                <div className="grid gap-3" style={{ gridTemplateColumns: "minmax(0,1.1fr) minmax(0,1fr) minmax(0,1fr)" }}>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={d.enabled}
                    aria-label={`${t?.default} を使う`}
                    disabled={!canEdit || busy}
                    onClick={() => set(r.key, { enabled: !d.enabled })}
                    className="flex items-center gap-3 px-3 rounded-xl text-left"
                    style={{ minHeight: 52, background: "var(--color-panel)", border: "1px solid var(--color-border)" }}
                  >
                    <span className="relative inline-block h-7 w-12 rounded-full shrink-0" style={{ background: d.enabled ? "var(--color-ok)" : "var(--color-panel3)", border: "1px solid var(--color-border)" }}>
                      <span className="absolute top-1 h-5 w-5 rounded-full bg-white" style={{ left: d.enabled ? 26 : 4, boxShadow: "0 1px 2px rgba(0,0,0,0.4)" }} />
                    </span>
                    <span>
                      使う <span className="text-sm font-bold">（{d.enabled ? "はい" : "いいえ"}）</span>
                    </span>
                  </button>
                  <label className="block">
                    <span className="label" style={{ fontSize: "0.9rem" }}>
                      画面に出す名前（空欄 = {t?.default}）
                    </span>
                    <input className="input" value={d.name} maxLength={12} placeholder={t?.default} disabled={!canEdit || busy} onChange={(e) => set(r.key, { name: e.target.value })} aria-label={`${t?.default} の名前`} />
                  </label>
                  <label className="block">
                    <span className="label" style={{ fontSize: "0.9rem" }}>
                      {shown}の料金（円）
                    </span>
                    <input
                      className="input num"
                      inputMode="numeric"
                      value={d.fee}
                      placeholder="未設定"
                      disabled={!canEdit || busy}
                      onChange={(e) => set(r.key, { fee: e.target.value.replace(/[^\d]/g, "") })}
                      aria-label={`${t?.default} の料金`}
                    />
                  </label>
                </div>
                {d.enabled && d.fee === "" && (
                  <p className="text-sm" style={{ color: "var(--color-warn, var(--color-danger))" }}>
                    料金が未設定のため、このままでは登録できません。料金なしなら 0 を入れてください。
                  </p>
                )}
              </div>
            );
          })}
      </div>
      <div className="p-2 border-t flex items-center gap-3" style={{ borderColor: "var(--color-border)" }}>
        <span className="text-sm flex-1" style={{ color: error ? "var(--color-danger)" : "var(--color-muted)" }} role={error ? "alert" : undefined}>
          {error ?? "保存のときにパスワードの再確認が出ることがあります。"}
        </span>
        {canEdit && (
          <button className="btn btn-lg btn-primary" style={{ minWidth: 220 }} onClick={save} disabled={busy || !draft}>
            {busy ? "保存中…" : "決定"}
          </button>
        )}
      </div>
    </section>
  );
}

/** 項目が 1 つだけの設定: 中央と右を合わせた広いフォーム（店舗設定。保存にはパスワードの再確認が必要） */
function SettingsForm({ section, pane }: { section: FormSection; pane: React.CSSProperties }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const canEdit = can("settings.edit");
  const st = useLoad<StoreSettings>("/api/store/settings");
  const [form, setForm] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (st.data) setForm(Object.fromEntries(section.fields.map((f) => [f.key, (st.data as unknown as Row)[f.key]])));
  }, [st.data, section]);

  async function save() {
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      const body: Row = {};
      for (const f of section.fields) {
        let v = form[f.key];
        if (f.type === "moneyOrNull") v = v === "" || v === null || v === undefined ? null : String(v);
        if (f.type === "int") v = Number(v || 0);
        body[f.key] = v;
      }
      const d = await withReauth(() => api<StoreSettings>("/api/store/settings", { method: "PUT", body }));
      st.setData(d);
      toast.show({ message: `${section.label} を保存しました` });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col min-h-0" style={pane} aria-label={section.label}>
      <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]">{section.label}</h2>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <p style={{ color: "var(--color-muted)" }}>{section.intro}</p>
        <ErrorBox error={st.error} onRetry={() => st.reload(false)} />
        {st.loading && <Spinner />}
        {!canEdit && <p className="badge badge-warn">見るだけです（変更には店舗設定の編集権限が必要です）</p>}
        {form && (
          <div className="grid grid-cols-2 gap-3">
            {section.fields.map((f) => (
              <FieldInput key={f.key} spec={f} value={form[f.key]} row={form} ctx={{ categories: [] }} disabled={!canEdit || busy} onChange={(v) => setForm((x) => ({ ...(x ?? {}), [f.key]: v }))} />
            ))}
          </div>
        )}
      </div>
      <div className="p-2 border-t flex items-center gap-3" style={{ borderColor: "var(--color-border)" }}>
        <span className="text-sm flex-1" style={{ color: error ? "var(--color-danger)" : "var(--color-muted)" }} role={error ? "alert" : undefined}>
          {error ?? "保存のときにパスワードの再確認が出ることがあります。"}
        </span>
        {canEdit && (
          <button className="btn btn-lg btn-primary" style={{ minWidth: 220 }} onClick={save} disabled={busy || !form}>
            {busy ? "保存中…" : "決定"}
          </button>
        )}
      </div>
    </section>
  );
}

function FieldInput({ spec, value, row, ctx, disabled, onChange }: { spec: FieldSpec; value: unknown; row: Row; ctx: Ctx; disabled: boolean; onChange: (v: unknown) => void }) {
  const label = (
    <span className="label" style={{ fontSize: "0.9rem" }}>
      {spec.label}
      {spec.required && <span style={{ color: "var(--color-danger)" }}> *</span>}
    </span>
  );
  const hint = spec.hint && (
    <span className="block text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
      {spec.hint}
    </span>
  );
  const digits = (s: string) => s.replace(/[^\d.]/g, "");
  if (spec.type === "readonly") {
    return (
      <div>
        {label}
        <div className="card-2" style={{ padding: "0.5rem 0.7rem" }}>
          {spec.format ? spec.format(row) : String(value ?? "")}
        </div>
      </div>
    );
  }
  if (spec.type === "toggle") {
    const on = !!value;
    return (
      <button type="button" role="switch" aria-checked={on} aria-label={spec.label} disabled={disabled} onClick={() => onChange(!on)} className="flex items-center gap-3 px-3 rounded-xl w-full text-left" style={{ minHeight: 52, background: "var(--color-panel2)", border: "1px solid var(--color-border)" }}>
        <span className="relative inline-block h-7 w-12 rounded-full shrink-0" style={{ background: on ? "var(--color-ok)" : "var(--color-panel3)", border: "1px solid var(--color-border)" }}>
          <span className="absolute top-1 h-5 w-5 rounded-full bg-white" style={{ left: on ? 26 : 4, boxShadow: "0 1px 2px rgba(0,0,0,0.4)" }} />
        </span>
        <span>
          {spec.label} <span className="text-sm font-bold">（{on ? "はい" : "いいえ"}）</span>
        </span>
      </button>
    );
  }
  if (spec.type === "color") {
    return (
      <div>
        {label}
        <div className="flex flex-wrap gap-2" role="group" aria-label={spec.label}>
          {COLORS.map((c) => (
            <button key={c || "none"} type="button" disabled={disabled} onClick={() => onChange(c)} aria-pressed={(value ?? "") === c} aria-label={c ? `色 ${c}` : "色なし"} className="rounded-full flex items-center justify-center text-xs" style={{ width: 44, height: 44, background: c || "var(--color-panel2)", border: (value ?? "") === c ? "4px solid var(--color-text)" : "2px solid rgba(0,0,0,0.25)" }}>
              {c ? "" : "なし"}
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (spec.type === "select") {
    const options = typeof spec.options === "function" ? spec.options(ctx) : spec.options ?? [];
    return (
      <label className="block">
        {label}
        <select className="input" value={value === null || value === undefined ? "" : String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {spec.key === "category" && <option value="">選んでください</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {hint}
      </label>
    );
  }
  if (spec.type === "minutesOrUnlimited") {
    const unlimited = value === null || value === undefined || value === "";
    return (
      <div>
        {label}
        <div className="flex items-center gap-2">
          <button type="button" className={`btn ${unlimited ? "btn-primary" : ""}`} disabled={disabled} onClick={() => onChange(unlimited ? 60 : null)} aria-pressed={unlimited}>
            時間無制限
          </button>
          {!unlimited && (
            <>
              <input className="input num" style={{ maxWidth: 130 }} inputMode="numeric" value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value.replace(/[^\d]/g, "") === "" ? "" : Number(e.target.value.replace(/[^\d]/g, "")))} aria-label="時間（分）" />
              <span>分</span>
            </>
          )}
        </div>
      </div>
    );
  }
  if (spec.type === "extOptions") {
    const list = (value as { minutes: number | string; price: string }[]) ?? [];
    const set = (i: number, patch: Partial<{ minutes: number | string; price: string }>) => onChange(list.map((o, j) => (j === i ? { ...o, ...patch } : o)));
    return (
      <div role="group" aria-label={spec.label}>
        {label}
        <div className="space-y-1.5">
          {list.map((o, i) => (
            <div key={i} className="flex items-center gap-2">
              <input className="input num" style={{ maxWidth: 110 }} inputMode="numeric" value={String(o.minutes ?? "")} disabled={disabled} onChange={(e) => set(i, { minutes: e.target.value.replace(/[^\d]/g, "") })} aria-label={`延長 ${i + 1} の分数`} />
              <span>分</span>
              <input className="input num" style={{ maxWidth: 150 }} inputMode="numeric" value={String(o.price ?? "").replace(/\.00$/, "")} disabled={disabled} onChange={(e) => set(i, { price: e.target.value.replace(/[^\d]/g, "") })} aria-label={`延長 ${i + 1} の料金`} />
              <span>円</span>
              <button type="button" className="btn btn-minus" style={{ minHeight: 46, minWidth: 46, padding: 0 }} disabled={disabled} onClick={() => onChange(list.filter((_, j) => j !== i))} aria-label={`延長 ${i + 1} を消す`}>
                −
              </button>
            </div>
          ))}
          <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => onChange([...list, { minutes: "", price: "" }])}>
            ＋ 延長の候補を足す
          </button>
        </div>
        {hint}
      </div>
    );
  }
  const numeric = spec.type !== "text";
  const shown = value === null || value === undefined ? "" : spec.type === "money" || spec.type === "moneyOrNull" ? String(value).replace(/\.00$/, "") : String(value);
  return (
    <label className="block">
      {label}
      <input
        className={`input ${numeric ? "num" : ""}`}
        inputMode={numeric ? "decimal" : undefined}
        value={shown}
        disabled={disabled}
        placeholder={spec.placeholder ?? (spec.type === "moneyOrNull" ? "未設定" : undefined)}
        onChange={(e) => onChange(spec.type === "text" ? e.target.value : spec.type === "int" ? e.target.value.replace(/[^\d]/g, "") : digits(e.target.value))}
        onFocus={(e) => e.currentTarget.scrollIntoView({ block: "center", behavior: "smooth" })}
        aria-label={spec.label}
      />
      {hint}
    </label>
  );
}
