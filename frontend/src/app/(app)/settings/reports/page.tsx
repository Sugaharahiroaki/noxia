"use client";

import { useEffect, useState } from "react";
import { RequireCaps } from "@/components/common";
import { useToast } from "@/components/Toast";
import { ConfirmDialog, Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useTerms } from "@/lib/terms";
import type { CashItem, TermRow } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

/** 入出金項目のマスタと名称変更（POS・集計画面の業務用語）。変更は店舗設定の編集権限が必要 */
export default function ReportSettingsPage() {
  return (
    <RequireCaps caps={["settings.view"]}>
      <div className="max-w-5xl space-y-4">
        <PageHeader title="入出金項目・名称変更" subtitle="レジの入出金を分けて記録するための項目と、POS・集計画面で使う用語の言い換えです。経理・税務の画面には影響しません。" />
        <CashItemsCard />
        <TermsCard />
      </div>
    </RequireCaps>
  );
}

function CashItemsCard() {
  const { can } = useSession();
  const toast = useToast();
  const canEdit = can("settings.edit");
  const q = useLoad<CashItem[]>(can("closing.operate") ? "/api/store/cash-items" : null);
  const [name, setName] = useState("");
  const [direction, setDirection] = useState<"in" | "out">("out");
  const [inProfit, setInProfit] = useState(true);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      toast.show({ message: done });
      await q.reload(true);
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  const items = q.data ?? [];
  return (
    <section className="card space-y-3" aria-label="入出金項目">
      <div>
        <h2 className="text-xl font-bold">入出金項目</h2>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          「開店・日締め・現金」で「その他入金」「その他出金」を登録するときに選べる項目です。「粗利に含める」をオフにした項目（両替・立替など）は、収支の粗利に入りません。
          レジの現金の計算（理論現金）は項目に関係なく、これまでどおり入金・出金として数えます。設定を変えても、登録済みの入出金の扱いは変わりません。
        </p>
      </div>
      {!can("closing.operate") && <Empty>入出金項目を見る権限がありません。</Empty>}
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && can("closing.operate") && <Spinner />}
      {q.data && (
        <div className="scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>項目名</th>
                <th>入金／出金</th>
                <th>粗利に含める</th>
                <th>状態</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} style={{ opacity: it.is_active ? 1 : 0.55 }}>
                  <td className="font-semibold">{it.name}</td>
                  <td>{it.direction_label}</td>
                  <td>
                    <button
                      className={`btn btn-sm ${it.affects_profit ? "btn-primary" : ""}`}
                      disabled={!canEdit || busy}
                      aria-pressed={it.affects_profit}
                      aria-label={`${it.name} を粗利に含める`}
                      onClick={() => run(() => api(`/api/store/cash-items/${it.id}`, { method: "PATCH", body: { affects_profit: !it.affects_profit } }), `${it.name}: 粗利に${it.affects_profit ? "含めない" : "含める"}ようにしました`)}
                    >
                      {it.affects_profit ? "含める" : "含めない"}
                    </button>
                  </td>
                  <td>{it.is_active ? "使用中" : "停止中"}</td>
                  <td className="r">
                    {canEdit && (
                      <button
                        className="btn btn-sm"
                        disabled={busy}
                        onClick={() =>
                          it.is_active
                            ? run(() => api(`/api/store/cash-items/${it.id}`, { method: "DELETE" }), `${it.name} を停止しました`)
                            : run(() => api(`/api/store/cash-items/${it.id}`, { method: "PATCH", body: { is_active: true } }), `${it.name} を使用中に戻しました`)
                        }
                      >
                        {it.is_active ? "停止する" : "使用中に戻す"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length === 0 && <Empty>項目はまだありません。項目が無くても、入出金はこれまでどおり登録できます。</Empty>}
        </div>
      )}
      {canEdit ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="block">
            <span className="label">項目名</span>
            <input className="input" style={{ width: 220 }} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} aria-label="新しい項目名" />
          </label>
          <div className="flex gap-2" role="group" aria-label="入金／出金">
            <button className={`btn btn-sm ${direction === "in" ? "btn-primary" : ""}`} onClick={() => setDirection("in")} aria-pressed={direction === "in"}>
              入金
            </button>
            <button className={`btn btn-sm ${direction === "out" ? "btn-primary" : ""}`} onClick={() => setDirection("out")} aria-pressed={direction === "out"}>
              出金
            </button>
          </div>
          <button className={`btn btn-sm ${inProfit ? "btn-primary" : ""}`} onClick={() => setInProfit((v) => !v)} aria-pressed={inProfit}>
            粗利に{inProfit ? "含める" : "含めない"}
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !name.trim()}
            onClick={async () => {
              await run(() => api("/api/store/cash-items", { method: "POST", body: { name: name.trim(), direction, affects_profit: inProfit, sort_order: items.length } }), `${name.trim()} を追加しました`);
              setName("");
            }}
          >
            項目を追加
          </button>
        </div>
      ) : (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          変更には店舗設定の編集権限が必要です。
        </p>
      )}
    </section>
  );
}

function TermsCard() {
  const { can } = useSession();
  const toast = useToast();
  const terms = useTerms();
  const canEdit = can("settings.edit");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [askReset, setAskReset] = useState(false);
  useEffect(() => {
    setDraft(Object.fromEntries(terms.rows.map((r) => [r.key, r.label])));
  }, [terms.rows]);
  const changed = terms.rows.some((r) => (draft[r.key] ?? r.label).trim() !== r.label);

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      await terms.reload();
      toast.show({ message: done });
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-3" aria-label="名称変更">
      <div>
        <h2 className="text-xl font-bold">名称変更</h2>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          POS（卓・入店・明細）と集計画面・出力の見出しに出る用語を、お店の呼び方に言い換えます。計算は変わりません。
          伝票の明細に保存済みの名前（「指名料（名前）」など）と領収書、経理・税務の画面は変わりません。空にして保存すると、その用語だけ初期値に戻ります。
        </p>
      </div>
      {terms.rows.length === 0 && <Spinner />}
      <div className="space-y-2">
        {terms.rows.map((r: TermRow) => (
          <div key={r.key} className="flex flex-wrap items-center gap-2">
            <span className="font-semibold" style={{ minWidth: 110 }}>
              {r.default}
            </span>
            <span aria-hidden>→</span>
            <input
              className="input"
              style={{ width: 200 }}
              value={draft[r.key] ?? ""}
              maxLength={12}
              placeholder={r.default}
              disabled={!canEdit}
              onChange={(e) => setDraft((d) => ({ ...d, [r.key]: e.target.value }))}
              aria-label={`${r.default} の表示名`}
            />
            {r.overridden && <span className="badge">変更中</span>}
            <span className="text-sm" style={{ color: "var(--color-muted)" }}>
              {r.description}
            </span>
          </div>
        ))}
      </div>
      {canEdit ? (
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" disabled={busy || !changed} onClick={() => run(() => api("/api/store/terms", { method: "PUT", body: { labels: draft } }), "名称を保存しました")}>
            保存する
          </button>
          <button className="btn" disabled={busy || !terms.rows.some((r) => r.overridden)} onClick={() => setAskReset(true)}>
            すべて初期値に戻す
          </button>
        </div>
      ) : (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          変更には店舗設定の編集権限が必要です。
        </p>
      )}
      {askReset && (
        <ConfirmDialog
          title="名称をすべて初期値に戻しますか？"
          confirmLabel="初期値に戻す"
          busy={busy}
          onCancel={() => setAskReset(false)}
          onConfirm={async () => {
            await run(() => api("/api/store/terms", { method: "DELETE" }), "名称を初期値に戻しました");
            setAskReset(false);
          }}
        >
          <p>変更した名称（{terms.rows.filter((r) => r.overridden).map((r) => `${r.default} → ${r.label}`).join("、")}）を、初期値に戻します。</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
