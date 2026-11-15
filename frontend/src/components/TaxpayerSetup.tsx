"use client";

/**
 * 店舗に事業者（お店を運営している法人・個人事業主）がまだ無いときに、その場で登録して店舗に設定する。
 * 共通のエラー表示（ErrorBox の code=taxpayer_required）と源泉所得税管理の画面から使う。
 * ui.tsx から読み込まれるので、ここでは ui.tsx を import しない（循環を避ける）。
 */
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { TaxPayer } from "@/lib/types";

export function TaxpayerSetup() {
  const { store, can, withReauth } = useSession();
  const canEdit = can("accounting.settings");
  const [list, setList] = useState<TaxPayer[] | null>(null);
  const [mode, setMode] = useState<"existing" | "new">("new");
  const [existingId, setExistingId] = useState<number | "">("");
  const [name, setName] = useState(store?.company.name ?? "");
  const [entity, setEntity] = useState<"" | "corporation" | "sole_proprietor">("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!canEdit) return;
    api<TaxPayer[]>("/api/accounting/taxpayers")
      .then((l) => {
        setList(l);
        if (l.length > 0) {
          setMode("existing");
          setExistingId(l[0].id);
        }
      })
      .catch(() => setList([]));
  }, [canEdit]);

  async function save() {
    if (!store) return;
    setBusy(true);
    setErr(null);
    try {
      await withReauth(async () => {
        let id = mode === "existing" ? Number(existingId) : 0;
        if (mode === "new") {
          const tp = await api<TaxPayer>("/api/accounting/taxpayers", { method: "POST", body: { name: name.trim(), entity_type: entity } });
          id = tp.id;
        }
        await api(`/api/accounting/stores/${store.id}/taxpayer`, { method: "PUT", body: { taxpayer_id: id } });
      });
      // 店舗の事業者はセッション情報（メニューの出し分け）にも入っているので、画面ごと読み直す
      window.location.reload();
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  }

  const ready = mode === "existing" ? existingId !== "" : name.trim() !== "" && entity !== "";
  return (
    <div className="card space-y-3" style={{ borderColor: "var(--color-warn)" }} role="alert">
      <p className="text-lg font-semibold">この店舗の事業者がまだ登録されていません</p>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        事業者とは、このお店を運営している法人または個人事業主のことです。帳簿・損益・CSV はこの事業者の帳簿として作ります。
        1 店舗につき 1 回の設定で、働く人ごとの源泉徴収とは関係ありません（源泉徴収をしないお店でも必要です）。
      </p>
      {!canEdit && <p className="font-semibold">オーナーに「会計設定」で登録してもらってください。</p>}
      {canEdit && list === null && <p className="text-sm">読み込み中…</p>}
      {canEdit && list !== null && (
        <div className="space-y-3">
          {list.length > 0 && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className={`btn btn-sm ${mode === "existing" ? "btn-primary" : ""}`} onClick={() => setMode("existing")}>
                登録済みの事業者を使う
              </button>
              <button type="button" className={`btn btn-sm ${mode === "new" ? "btn-primary" : ""}`} onClick={() => setMode("new")}>
                新しく登録する
              </button>
            </div>
          )}
          {mode === "existing" && list.length > 0 && (
            <label className="block max-w-md">
              <span className="label">事業者</span>
              <select className="input" value={existingId} onChange={(e) => setExistingId(Number(e.target.value))}>
                {list.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}（{t.entity_type_label}）
                  </option>
                ))}
              </select>
            </label>
          )}
          {mode === "new" && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl">
              <label className="block">
                <span className="label">事業者名（法人名、または屋号・氏名）</span>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <div>
                <span className="label">区分</span>
                <div className="flex gap-2">
                  <button type="button" className={`btn ${entity === "corporation" ? "btn-primary" : ""}`} onClick={() => setEntity("corporation")}>
                    法人
                  </button>
                  <button type="button" className={`btn ${entity === "sole_proprietor" ? "btn-primary" : ""}`} onClick={() => setEntity("sole_proprietor")}>
                    個人事業主
                  </button>
                </div>
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={save}>
              {busy ? "設定中…" : `この店舗（${store?.name ?? ""}）に設定する`}
            </button>
            <span className="text-xs" style={{ color: "var(--color-muted)" }}>
              決算月・所轄税務署・申告方法などは、あとから「会計設定」で変えられます。
            </span>
          </div>
          {err && (
            <p className="text-sm font-semibold" style={{ color: "var(--color-danger)" }}>
              {err}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
