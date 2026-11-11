"use client";

/**
 * 経理・申告画面の共通部品: タブ、コピー、理由の選択、権限ガード、チェックリスト、ステップ表示、年月の選択。
 * 営業中の画面と同じく、ボタンは大きく・状態は色と文字の両方で示す。
 */
import { useEffect, useState } from "react";
import { useSession } from "@/lib/session";

export type TabDef = { key: string; label: string; badge?: number | string; tone?: "warn" | "danger" | "ok" };

export function Tabs({ tabs, value, onChange, size = "md" }: { tabs: TabDef[]; value: string; onChange: (k: string) => void; size?: "sm" | "md" }) {
  return (
    <div className="flex flex-wrap gap-2 mb-3" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          className={`btn ${size === "sm" ? "btn-sm" : ""} ${value === t.key ? "btn-primary" : ""}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {t.badge !== undefined && t.badge !== 0 && t.badge !== "" && (
            <span className={`badge ${t.tone === "danger" ? "badge-danger" : t.tone === "ok" ? "badge-ok" : "badge-warn"}`}>{t.badge}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/** 金額などをクリップボードにコピーする（国税庁の画面への転記用）。失敗したら値を選択状態で表示する。 */
export function CopyButton({ value, label = "コピー", size = "sm", onCopied }: { value: string; label?: string; size?: "sm" | "md"; onCopied?: () => void }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 1800);
    return () => clearTimeout(t);
  }, [state]);
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        className={`btn ${size === "sm" ? "btn-sm" : ""} ${state === "ok" ? "btn-ok" : ""}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setState("ok");
            onCopied?.();
          } catch {
            setState("fail");
          }
        }}
      >
        {state === "ok" ? "✓ コピーしました" : label}
      </button>
      {state === "fail" && (
        <input className="input" style={{ minHeight: 40, width: 160 }} readOnly value={value} onFocus={(e) => e.currentTarget.select()} autoFocus aria-label="コピーする値" />
      )}
    </span>
  );
}

/** 理由を大きなボタンで選ぶ（「その他」のときだけ入力欄） */
export function ReasonPicker({ options, value, onChange, otherLabel = "その他（入力）" }: { options: string[]; value: string; onChange: (v: string) => void; otherLabel?: string }) {
  const isOther = value !== "" && !options.includes(value);
  const [other, setOther] = useState(isOther);
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {options.map((o) => (
          <button key={o} type="button" className={`btn ${value === o && !other ? "btn-primary" : ""}`} onClick={() => { setOther(false); onChange(o); }}>
            {o}
          </button>
        ))}
        <button type="button" className={`btn ${other ? "btn-primary" : ""}`} onClick={() => { setOther(true); onChange(""); }}>
          {otherLabel}
        </button>
      </div>
      {other && <input className="input" placeholder="理由を入力" value={value} onChange={(e) => onChange(e.target.value)} />}
    </div>
  );
}

/** ページ単位の権限ガード（見栄え用。認可は常に API 側で行う） */
/** 申告方法が freee の事業者で、freee 側で行う画面（銀行照合・固定資産・年間決算・確定申告）を直接開いたときの案内 */
export function FreeeHandlesNotice({ what }: { what: string }) {
  const { store } = useSession();
  if (store?.taxpayer?.filing_method !== "freee") return null;
  return (
    <p className="badge badge-warn" style={{ whiteSpace: "normal", fontSize: "0.95rem" }}>
      この事業者（{store.taxpayer.name}）は「freee で申告」の設定です。{what}は freee 側で行い、NOXIA からは仕訳を渡します（freee 連携）。ここは参考表示で、メニューには出ていません。設定は「会計設定」で変えられます。
    </p>
  );
}

export function RequireCaps({ caps, anyCaps, children, message = "この画面を見る権限がありません。" }: { caps?: string[]; anyCaps?: string[]; children: React.ReactNode; message?: string }) {
  const { can, store, loading } = useSession();
  if (loading || !store) return null;
  const ok = (caps ? can(...caps) : true) && (anyCaps ? anyCaps.some((c) => can(c)) : true);
  if (!ok) {
    return (
      <div className="card" role="alert">
        <p className="font-semibold">{message}</p>
        <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
          必要な権限についてはオーナーに確認してください。
        </p>
      </div>
    );
  }
  return <>{children}</>;
}

export type ChecklistItem = { key: string; label: string; done: boolean; hint?: string; count?: number; blocking?: boolean };

export function Checklist({ items, onToggle }: { items: ChecklistItem[]; onToggle?: (key: string, done: boolean) => void }) {
  return (
    <ul className="space-y-2">
      {items.map((it) => (
        <li key={it.key} className="card-2 flex items-start gap-3">
          <button
            type="button"
            className={`btn btn-sm ${it.done ? "btn-ok" : ""}`}
            style={{ minWidth: 56 }}
            aria-pressed={it.done}
            onClick={() => onToggle?.(it.key, !it.done)}
            disabled={!onToggle}
          >
            {it.done ? "✓" : "　"}
          </button>
          <div className="flex-1">
            <div className="font-semibold">
              {it.label}
              {it.count !== undefined && it.count > 0 && <span className={`badge ml-2 ${it.blocking ? "badge-danger" : "badge-warn"}`}>{it.count} 件</span>}
            </div>
            {it.hint && (
              <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                {it.hint}
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** ステップ式の進み具合（1/4 撮影 → 2/4 確認 …） */
export function Wizard({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="flex flex-wrap items-center gap-2 mb-3" aria-label="手順">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-2">
          <span className={`badge ${i < current ? "badge-ok" : i === current ? "badge-accent" : ""}`} style={{ minHeight: 34, fontSize: "1rem" }}>
            {i + 1}/{steps.length} {s}
          </span>
          {i < steps.length - 1 && <span aria-hidden>→</span>}
        </li>
      ))}
    </ol>
  );
}

/** 年月の選択（◀ ▶ と month 入力） */
export function MonthPicker({ value, onChange }: { value: string; onChange: (ym: string) => void }) {
  const shift = (d: number) => {
    const [y, m] = value.split("-").map(Number);
    const dt = new Date(y, m - 1 + d, 1);
    onChange(`${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`);
  };
  return (
    <div className="flex items-center gap-2">
      <button className="btn btn-sm" onClick={() => shift(-1)} aria-label="前の月">
        ◀
      </button>
      <input type="month" className="input" style={{ minHeight: 44, width: 170 }} value={value} onChange={(e) => e.target.value && onChange(e.target.value)} />
      <button className="btn btn-sm" onClick={() => shift(1)} aria-label="次の月">
        ▶
      </button>
    </div>
  );
}

/** 年の選択 */
export function YearPicker({ value, onChange, min = 2024, max }: { value: number; onChange: (y: number) => void; min?: number; max?: number }) {
  const hi = max ?? new Date().getFullYear() + 1;
  return (
    <div className="flex items-center gap-2">
      <button className="btn btn-sm" onClick={() => onChange(Math.max(min, value - 1))} aria-label="前の年">
        ◀
      </button>
      <span className="text-xl font-bold num" style={{ minWidth: 90, textAlign: "center" }}>
        {value}年分
      </span>
      <button className="btn btn-sm" onClick={() => onChange(Math.min(hi, value + 1))} aria-label="次の年">
        ▶
      </button>
    </div>
  );
}

/** 項目ごとのエラー（ApiError.details）を表示する */
export function FieldErrors({ details }: { details: unknown }) {
  if (!details || typeof details !== "object") return null;
  const entries = Object.entries(details as Record<string, unknown>).filter(([, v]) => v);
  if (!entries.length) return null;
  return (
    <ul className="text-sm mt-1" style={{ color: "var(--color-danger)" }}>
      {entries.map(([k, v]) => (
        <li key={k}>
          {k}: {Array.isArray(v) ? v.join(" ") : typeof v === "string" ? v : JSON.stringify(v)}
        </li>
      ))}
    </ul>
  );
}
