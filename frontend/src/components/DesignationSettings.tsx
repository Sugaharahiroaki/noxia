"use client";

import { useEffect, useMemo, useState } from "react";
import { useToast } from "@/components/Toast";
import { ErrorBox, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useTerms } from "@/lib/terms";
import type { Money, StoreSettings } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

/**
 * 指名・同伴の一覧（呼び・同伴・本指名・場内指名）。1 行ずつ「使う・名前・お客様の料金・キャストのバック（なし／円／会計の%）」を決める。
 * - 使う・料金は店舗設定（settings.edit）、名前は名称変更（同じく settings.edit）、バックは店舗共通のバックルール（backs.rules.edit。見るのは backs.rules.view）。
 * - バックの中身は /api/staff/designation-backs（サーバーが BackRule の店舗共通ルールに保存する。キャスト個人別のルールはそのまま）。
 * - 金額の計算はしない（入力をそのまま送り、計算はサーバー）。
 * PC の「時給・バック・料金」とタブレットの「POS 設定」の両方で使う。
 */

type Kind = "yobi" | "douhan" | "nomination" | "jounai";
type Mode = "none" | "fixed" | "percent";
type Basis = "set_extension" | "subtotal" | "total";
type Split = "equal" | "full_each";
type BackApiRow = {
  target: string;
  kind: "per_unit" | "subtotal";
  mode: Mode;
  value: Money | null;
  subtotal_basis: Basis;
  split_mode: Split;
  include_discount: boolean;
  rule_id: number | null;
  effective_from: string | null;
  personal_rules: number;
};
type BackDraft = { mode: Mode | "custom"; value: string; basis: Basis; split: Split };
type RowDraft = { enabled: boolean; name: string; fee: string; back: BackDraft };

const ROWS: { key: Kind; hint: string }[] = [
  { key: "yobi", hint: "キャストが呼んだお客様" },
  { key: "douhan", hint: "キャストと一緒に来店" },
  { key: "nomination", hint: "来店前から決まっている指名" },
  { key: "jounai", hint: "来店後に席で受けた指名" },
];
const FIELDS = {
  yobi: { enabled: "yobi_enabled", fee: "yobi_fee" },
  douhan: { enabled: "douhan_enabled", fee: "douhan_fee" },
  nomination: { enabled: "nomination_enabled", fee: "nomination_fee" },
  jounai: { enabled: "jounai_enabled", fee: "jounai_fee" },
} as const;
const BASIS_LABELS: Record<Basis, string> = { set_extension: "セット＋延長", subtotal: "会計（TAX前）", total: "会計（TAX込み）" };
const SPLIT_LABELS: Record<Split, string> = { equal: "人数で割る", full_each: "全員に全額" };

const num = (v: Money | null | undefined) => (v === null || v === undefined ? "" : String(Number(v)));

/** サーバーの 2 つのルール（1 回ごと・会計の%）から、一覧の 1 行の「なし／円／%」を決める。どちらにも当てはまらない組み合わせは custom */
function toDraft(unit: BackApiRow | undefined, bill: BackApiRow | undefined): BackDraft {
  const basis = bill?.subtotal_basis ?? "subtotal";
  const split = bill?.split_mode ?? "equal";
  const u = unit?.mode ?? "none";
  const b = bill?.mode ?? "none";
  if (u === "none" && b === "none") return { mode: "none", value: "", basis, split };
  if (u === "fixed" && b === "none") return { mode: "fixed", value: num(unit?.value), basis, split };
  if (u === "none" && b === "percent") return { mode: "percent", value: num(bill?.value), basis, split };
  return { mode: "custom", value: "", basis, split };
}

function customText(unit: BackApiRow | undefined, bill: BackApiRow | undefined): string {
  const parts: string[] = [];
  if (unit && unit.mode !== "none") parts.push(unit.mode === "fixed" ? `1 回 ${num(unit.value)}円` : `料金の ${num(unit.value)}%`);
  if (bill && bill.mode !== "none") parts.push(bill.mode === "percent" ? `会計の ${num(bill.value)}%` : `1 会計 ${num(bill.value)}円`);
  return parts.join(" ＋ ");
}

function sameBack(a: BackDraft, b: BackDraft) {
  if (a.mode !== b.mode) return false;
  if (a.mode === "none" || a.mode === "custom") return true;
  if (a.value.trim() !== b.value.trim() && Number(a.value) !== Number(b.value)) return false;
  return a.mode === "fixed" || (a.basis === b.basis && a.split === b.split);
}

export function DesignationSettings({ variant, title = "指名・同伴（呼び・同伴・本指名・場内指名）" }: { variant: "page" | "pane"; title?: string }) {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const terms = useTerms();
  const canEdit = can("settings.edit");
  const canSeeBacks = can("backs.rules.view");
  const canEditBacks = can("backs.rules.edit");
  const st = useLoad<StoreSettings>("/api/store/settings");
  const backs = useLoad<{ business_date: string; backs: BackApiRow[] }>(canSeeBacks ? "/api/staff/designation-backs" : null);
  const [draft, setDraft] = useState<Record<Kind, RowDraft> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const termOf = (key: string) => terms.rows.find((r) => r.key === key);
  const backOf = useMemo(() => {
    const map = new Map((backs.data?.backs ?? []).map((b) => [b.target, b]));
    return (k: Kind) => ({ unit: map.get(k), bill: map.get(`${k}_subtotal`) });
  }, [backs.data]);
  const initialBack = useMemo(() => Object.fromEntries(ROWS.map((r) => [r.key, toDraft(backOf(r.key).unit, backOf(r.key).bill)])) as Record<Kind, BackDraft>, [backOf]);
  const ready = !!st.data && terms.rows.length > 0 && (!canSeeBacks || !!backs.data);

  useEffect(() => {
    if (!ready || !st.data) return;
    const d = st.data;
    setDraft(
      Object.fromEntries(
        ROWS.map((r) => {
          const t = termOf(r.key);
          return [r.key, { enabled: d[FIELDS[r.key].enabled] !== false, name: t?.overridden ? t.label : "", fee: num(d[FIELDS[r.key].fee]), back: initialBack[r.key] }];
        }),
      ) as Record<Kind, RowDraft>,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, st.data, terms.rows, initialBack]);

  const set = (k: Kind, patch: Partial<RowDraft>) => setDraft((x) => (x ? { ...x, [k]: { ...x[k], ...patch } } : x));
  const setBack = (k: Kind, patch: Partial<BackDraft>) => setDraft((x) => (x ? { ...x, [k]: { ...x[k], back: { ...x[k].back, ...patch } } } : x));

  function problem(): string | null {
    if (!draft) return null;
    const seen = new Map<string, Kind>();
    for (const r of ROWS) {
      const name = draft[r.key].name.trim() || termOf(r.key)?.default || r.key;
      const other = seen.get(name);
      if (other) return `「${name}」が 2 つの項目に付いています。${termOf(r.key)?.default === name ? termOf(other)?.default : termOf(r.key)?.default}の名前を変えてください`;
      seen.set(name, r.key);
    }
    for (const r of ROWS) {
      const b = draft[r.key].back;
      const name = draft[r.key].name.trim() || termOf(r.key)?.default || r.key;
      if ((b.mode === "fixed" || b.mode === "percent") && b.value.trim() === "") return `${name}のバックの${b.mode === "fixed" ? "金額" : "%"}を入れてください`;
      if (b.mode === "percent" && Number(b.value) > 100) return `${name}のバックは 100% 以下にしてください`;
    }
    return null;
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      if (canEdit) {
        // 名前（名称変更）を先に保存する。名前で断られたら料金・オンオフ・バックも保存しない
        const labels: Record<string, string> = Object.fromEntries(terms.rows.filter((r) => r.overridden).map((r) => [r.key, r.label]));
        for (const r of ROWS) labels[r.key] = draft[r.key].name.trim();
        await withReauth(() => api("/api/store/terms", { method: "PUT", body: { labels } }));
        const body: Record<string, unknown> = {};
        for (const r of ROWS) {
          body[FIELDS[r.key].enabled] = draft[r.key].enabled;
          body[FIELDS[r.key].fee] = draft[r.key].fee === "" ? null : draft[r.key].fee;
        }
        st.setData(await withReauth(() => api<StoreSettings>("/api/store/settings", { method: "PUT", body })));
      }
      if (canEditBacks) {
        const items: Record<string, unknown>[] = [];
        for (const r of ROWS) {
          const b = draft[r.key].back;
          if (b.mode === "custom" || sameBack(b, initialBack[r.key])) continue;
          items.push({ target: r.key, mode: b.mode === "fixed" ? "fixed" : "none", value: b.mode === "fixed" ? b.value : null });
          items.push(
            b.mode === "percent"
              ? { target: `${r.key}_subtotal`, mode: "percent", value: b.value, subtotal_basis: b.basis, split_mode: b.split }
              : { target: `${r.key}_subtotal`, mode: "none" },
          );
        }
        if (items.length) backs.setData(await withReauth(() => api<{ business_date: string; backs: BackApiRow[] }>("/api/staff/designation-backs", { method: "PUT", body: { backs: items } })));
      }
      await terms.reload();
      toast.show({ message: "指名・同伴の設定を保存しました" });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const issue = problem();
  const body = (
    <div className="space-y-2">
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        使わない項目は入店・卓の画面に出しません。料金はお客様に請求する額で、空欄は「未設定」（選べません）、0 は料金なし。
        バックは「1 回いくら（円）」か「その項目が付いた会計の何%」を選びます。決めた日の営業日から使われ、それより前の分は変わりません。
      </p>
      <ErrorBox
        error={st.error || backs.error}
        onRetry={() => {
          st.reload(false);
          backs.reload(false);
        }}
      />
      {!draft && <Spinner />}
      {draft && !canEdit && <p className="badge badge-warn">見るだけです（料金・名前の変更には店舗設定の編集権限が必要です）</p>}
      {draft && canSeeBacks && !canEditBacks && <p className="badge badge-warn">バックは見るだけです（変更はオーナー）</p>}
      {draft &&
        ROWS.map((r) => {
          const t = termOf(r.key);
          const d = draft[r.key];
          const shown = d.name.trim() || t?.default || r.key;
          const { unit, bill } = backOf(r.key);
          const personal = (unit?.personal_rules ?? 0) + (bill?.personal_rules ?? 0);
          const lockBack = !canEditBacks || busy;
          return (
            <div key={r.key} className="card-2 space-y-2" role="group" aria-label={`${t?.default ?? r.key} の設定`} style={{ opacity: d.enabled ? 1 : 0.65, padding: "10px 12px" }}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xl font-bold" style={{ minWidth: 88 }}>
                  {shown}
                  {t && shown !== t.default && <span className="text-sm font-normal">（もとの名前: {t.default}）</span>}
                </span>
                <span className="text-sm" style={{ color: "var(--color-muted)" }}>
                  {r.hint}
                </span>
                <label className="flex items-center gap-2 ml-auto text-sm">
                  名前
                  <input
                    className="input"
                    style={{ width: 130, minHeight: 44 }}
                    value={d.name}
                    maxLength={12}
                    placeholder={t?.default}
                    disabled={!canEdit || busy}
                    onChange={(e) => set(r.key, { name: e.target.value })}
                    aria-label={`${t?.default} の名前`}
                  />
                </label>
                <button
                  type="button"
                  role="switch"
                  aria-checked={d.enabled}
                  aria-label={`${t?.default} を使う`}
                  disabled={!canEdit || busy}
                  onClick={() => set(r.key, { enabled: !d.enabled })}
                  className="flex items-center gap-2 px-3 rounded-xl"
                  style={{ minHeight: 44, background: "var(--color-panel)", border: "1px solid var(--color-border)" }}
                >
                  <span className="relative inline-block h-7 w-12 rounded-full shrink-0" style={{ background: d.enabled ? "var(--color-ok)" : "var(--color-panel3)", border: "1px solid var(--color-border)" }}>
                    <span className="absolute top-1 h-5 w-5 rounded-full bg-white" style={{ left: d.enabled ? 26 : 4, boxShadow: "0 1px 2px rgba(0,0,0,0.4)" }} />
                  </span>
                  <span className="font-bold">{d.enabled ? "使う" : "使わない"}</span>
                </button>
              </div>
              {d.enabled && (
                <div className="grid gap-2 items-end" style={{ gridTemplateColumns: "minmax(0,0.8fr) minmax(0,1.8fr) minmax(0,0.8fr) minmax(0,1fr) minmax(0,0.9fr)" }}>
                  <label className="block">
                    <span className="label" style={{ fontSize: "0.85rem" }}>
                      お客様の料金
                    </span>
                    <span className="flex items-center gap-1">
                      <input
                        className="input num"
                        inputMode="numeric"
                        value={d.fee}
                        placeholder="未設定"
                        disabled={!canEdit || busy}
                        onChange={(e) => set(r.key, { fee: e.target.value.replace(/[^\d]/g, "") })}
                        aria-label={`${shown} の料金（円）`}
                      />
                      円
                    </span>
                  </label>
                  {canSeeBacks ? (
                    <>
                      <div>
                        <span className="label" style={{ fontSize: "0.85rem" }}>
                          キャストのバック
                        </span>
                        <div className="grid grid-cols-3 gap-1" role="radiogroup" aria-label={`${shown} のバックの決め方`}>
                          {(
                            [
                              ["none", "なし"],
                              ["fixed", "円"],
                              ["percent", "会計の%"],
                            ] as const
                          ).map(([m, label]) => (
                            <button
                              key={m}
                              type="button"
                              role="radio"
                              aria-checked={d.back.mode === m}
                              disabled={lockBack}
                              className={`btn whitespace-nowrap ${d.back.mode === m ? "btn-primary" : ""}`}
                              style={{ minHeight: 44, paddingInline: 4 }}
                              onClick={() => setBack(r.key, { mode: m, value: m === initialBack[r.key].mode ? initialBack[r.key].value : "" })}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {d.back.mode === "fixed" || d.back.mode === "percent" ? (
                        <label className="block">
                          <span className="label" style={{ fontSize: "0.85rem" }}>
                            {d.back.mode === "fixed" ? "1 回あたり" : "会計の"}
                          </span>
                          <span className="flex items-center gap-1">
                            <input
                              className="input num"
                              inputMode="decimal"
                              value={d.back.value}
                              disabled={lockBack}
                              onChange={(e) => setBack(r.key, { value: e.target.value.replace(d.back.mode === "fixed" ? /[^\d]/g : /[^\d.]/g, "") })}
                              aria-label={`${shown} のバック（${d.back.mode === "fixed" ? "円" : "%"}）`}
                            />
                            {d.back.mode === "fixed" ? "円" : "%"}
                          </span>
                        </label>
                      ) : (
                        <div className="text-sm self-center" style={{ gridColumn: "span 3", color: "var(--color-muted)" }}>
                          {d.back.mode === "custom" ? `細かい設定があります（${customText(unit, bill)}）。変えるときは左で選び直してください。` : "バックは付きません"}
                          {personal > 0 && `　キャスト個人別の設定 ${personal} 件はそのまま（個人別が優先）`}
                        </div>
                      )}
                      {d.back.mode === "percent" && (
                        <>
                          <label className="block">
                            <span className="label" style={{ fontSize: "0.85rem" }}>
                              何の%か
                            </span>
                            <select className="input" value={d.back.basis} disabled={lockBack} onChange={(e) => setBack(r.key, { basis: e.target.value as Basis })} aria-label={`${shown} のバックの基準`}>
                              {(Object.keys(BASIS_LABELS) as Basis[]).map((k) => (
                                <option key={k} value={k}>
                                  {BASIS_LABELS[k]}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="block">
                            <span className="label" style={{ fontSize: "0.85rem" }}>
                              キャストが複数
                            </span>
                            <select className="input" value={d.back.split} disabled={lockBack} onChange={(e) => setBack(r.key, { split: e.target.value as Split })} aria-label={`${shown} のバックを複数人で`}>
                              {(Object.keys(SPLIT_LABELS) as Split[]).map((k) => (
                                <option key={k} value={k}>
                                  {SPLIT_LABELS[k]}
                                </option>
                              ))}
                            </select>
                          </label>
                        </>
                      )}
                      {d.back.mode === "fixed" && (
                        <div className="text-sm self-center" style={{ gridColumn: "span 2", color: "var(--color-muted)" }}>
                          {`${shown} 1 回ごとに付きます`}
                          {personal > 0 && `（キャスト個人別 ${personal} 件が優先）`}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="text-sm self-center" style={{ gridColumn: "span 4", color: "var(--color-muted)" }}>
                      キャストのバックはオーナーが「時給・バック・料金」で設定します。
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
    </div>
  );

  const footer = (
    <div className="flex items-center gap-3">
      <span className="text-sm flex-1" style={{ color: error || issue ? "var(--color-danger)" : "var(--color-muted)" }} role={error ? "alert" : undefined}>
        {error ?? issue ?? "保存のときにパスワードの再確認が出ることがあります。"}
      </span>
      {(canEdit || canEditBacks) && (
        <button className="btn btn-lg btn-primary" style={{ minWidth: 220 }} onClick={save} disabled={busy || !draft || !!issue}>
          {busy ? "保存中…" : variant === "pane" ? "決定" : "保存"}
        </button>
      )}
    </div>
  );

  if (variant === "pane") {
    return (
      <section className="flex flex-col min-h-0" style={{ background: "var(--color-panel)", border: "1.5px solid var(--color-border)", borderRadius: 14 }} aria-label={title}>
        <h2 className="pos-bar px-3 py-1.5 font-bold text-base rounded-t-[12px]">{title}</h2>
        <div className="flex-1 overflow-y-auto p-2">{body}</div>
        <div className="p-2 border-t" style={{ borderColor: "var(--color-border)" }}>
          {footer}
        </div>
      </section>
    );
  }
  return (
    <section className="card space-y-3" aria-label={title}>
      <h2 className="font-bold">{title}</h2>
      {body}
      {footer}
    </section>
  );
}
