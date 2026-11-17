"use client";

/**
 * 伝票のプレビューと印刷（概算伝票・会計伝票・領収書・精算伝票）。
 * 中身と金額はサーバーが作る（画面では計算しない）。印刷はエプソンのアプリ（TM Print Assistant）に渡す。
 * プリンターを使わない店でも、概算は画面で見せられる。
 */
import { useEffect, useRef, useState } from "react";
import { ErrorBox, Field, Modal, Spinner } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime, yen } from "@/lib/format";
import { sendToPrinter } from "@/lib/printing";
import type { EstimateTier, PrintResponse, PrintSlip, PrinterConfig } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

/** 店舗のプリンター設定（印刷ボタンを出すかどうか） */
export function usePrinter() {
  const q = useLoad<PrinterConfig>("/api/printing/config", [], { refetchOnFocus: false });
  const cfg = q.data;
  return {
    config: cfg,
    can: (kind: "estimate" | "receipt" | "ryoshusho" | "closing") =>
      !!cfg?.enabled && !!{ estimate: cfg.print_estimate, receipt: cfg.print_receipt, ryoshusho: cfg.print_ryoshusho, closing: cfg.print_closing }[kind],
  };
}

/** 紙の見た目のプレビュー（行の中身はサーバーの slip そのまま） */
export function SlipPreview({ slip }: { slip: PrintSlip }) {
  return (
    <div
      className="mx-auto rounded-md shadow-sm"
      style={{ background: "#fff", color: "#111", width: slip.columns <= 35 ? 270 : 360, padding: "14px 14px 20px", border: "1px solid #ddd", fontSize: 14, lineHeight: 1.45 }}
      data-testid={`slip-${slip.kind}`}
      aria-label={`${slip.title}のプレビュー`}
    >
      {slip.rows.map((r, i) => {
        const style = { fontSize: r.size === 2 ? 24 : 14, fontWeight: r.bold ? 700 : 400 } as const;
        if (r.kind === "rule") return <div key={i} style={{ borderTop: "1px dashed #888", margin: "6px 0" }} />;
        if (r.kind === "feed") return <div key={i} style={{ height: 10 }} />;
        if (r.kind === "stamp")
          return (
            <div key={i} className="my-2 flex items-center justify-center" style={{ width: 64, height: 64, border: "1px dashed #555", fontSize: 11 }}>
              収入印紙
            </div>
          );
        if (r.kind === "pair")
          return (
            <div key={i} className="flex justify-between gap-3" style={style}>
              <span>{r.left}</span>
              <span className="num whitespace-nowrap">{r.right}</span>
            </div>
          );
        return (
          <div key={i} style={{ ...style, textAlign: r.align, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {r.left}
          </div>
        );
      })}
    </div>
  );
}

/** 「印刷する」ボタン（エプソンのアプリを開く） */
export function PrintButton({ resp, label = "印刷する", className = "btn btn-xl btn-primary", onPrinted }: { resp: PrintResponse | null; label?: string; className?: string; onPrinted?: () => void }) {
  const [sent, setSent] = useState(false);
  if (!resp) return null;
  if (!resp.printable) {
    return (
      <span className="text-sm self-center" style={{ color: "var(--color-muted)" }}>
        印刷は「店舗設定 → プリンター」で使う設定にすると出せます
      </span>
    );
  }
  return (
    <>
      {sent && (
        <span className="text-sm self-center" style={{ color: "var(--color-muted)" }}>
          エプソンのアプリで印刷します。終わったら画面左上の「◀」で NOXIA に戻ります
        </span>
      )}
      <button
        className={className}
        onClick={() => {
          setSent(true);
          sendToPrinter(resp.epos_xml);
          onPrinted?.();
        }}
        data-testid="print-button"
      >
        🖨 {label}
      </button>
    </>
  );
}

/** 伝票を読み込んでプレビューと印刷（会計伝票・精算伝票・テスト印刷） */
export function PrintDialog({ title, path, onClose }: { title: string; path: string; onClose: () => void }) {
  const q = useLoad<PrintResponse>(path, [], { refetchOnFocus: false });
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose}>
            閉じる
          </button>
          <PrintButton resp={q.data} />
        </>
      }
    >
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner label="伝票を作っています…" />}
      {q.data && <SlipPreview slip={q.data.slip} />}
    </Modal>
  );
}

/** 概算（いまお会計なら／延長したら）。お客様に画面で見せ、必要なら印刷して渡す */
export function EstimateDialog({ visitId, tableName, onClose }: { visitId: number; tableName: string; onClose: () => void }) {
  const q = useLoad<PrintResponse>(`/api/visits/${visitId}/print/estimate`, [], { refetchOnFocus: false });
  const [showSlip, setShowSlip] = useState(false);
  const tiers = q.data?.tiers ?? [];
  return (
    <Modal
      title={`${tableName} のお会計の目安`}
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={() => setShowSlip((v) => !v)} disabled={!q.data}>
            {showSlip ? "金額だけ表示" : "伝票の見た目"}
          </button>
          <button className="btn btn-xl" onClick={onClose}>
            閉じる
          </button>
          <PrintButton resp={q.data} label="概算伝票を印刷" />
        </>
      }
    >
      <ErrorBox error={q.error} onRetry={() => q.reload(false)} />
      {q.loading && <Spinner label="金額を計算しています…" />}
      {q.data && showSlip && <SlipPreview slip={q.data.slip} />}
      {q.data && !showSlip && (
        <div className={`grid gap-3 ${tiers.length >= 3 ? "grid-cols-3" : tiers.length === 2 ? "grid-cols-2" : "grid-cols-1"}`} data-testid="estimate-tiers">
          {tiers.map((t) => (
            <TierCard key={t.minutes} tier={t} />
          ))}
        </div>
      )}
      {q.data && !showSlip && (
        <p className="text-sm mt-3" style={{ color: "var(--color-muted)" }}>
          税・サービス料を含む金額です。ご注文を追加すると変わります。延長の分数と料金は「POS 設定 → セット構成」の延長の候補です。
        </p>
      )}
    </Modal>
  );
}

function TierCard({ tier }: { tier: EstimateTier }) {
  const now = tier.minutes === 0;
  const bm = tier.by_method;
  return (
    <div className="card-2 text-center space-y-1" style={now ? { borderColor: "var(--color-accent)", borderWidth: 2 } : undefined} data-testid={`tier-${tier.minutes}`}>
      <div className="text-xl font-bold">{now ? "いまお会計なら" : `${tier.minutes}分 延長したら`}</div>
      <div className="text-base num" style={{ color: "var(--color-muted)" }}>
        {tier.planned_end_at ? `${fmtTime(tier.planned_end_at)} まで` : "時間無制限"}
      </div>
      <div className="font-bold num" style={{ fontSize: "2.6rem", lineHeight: 1.1, color: now ? undefined : "var(--color-orange)" }}>
        {yen(tier.total)}
      </div>
      {bm.cash && bm.card && (
        <div className="text-sm num" style={{ color: "var(--color-muted)" }}>
          現金 {yen(bm.cash)} ・ カード {yen(bm.card)}
        </div>
      )}
      {tier.extended_entries.length > 0 && !now && (
        <div className="text-xs" style={{ color: "var(--color-muted)" }}>
          延長するセット: {tier.extended_entries.join("・")}
        </div>
      )}
    </div>
  );
}

/** 領収書（宛名・但し書きを入れて発行。2 回目からは「再発行」と印字） */
export function RyoshushoDialog({ checkoutId, onClose }: { checkoutId: number; onClose: () => void }) {
  const [addressee, setAddressee] = useState("");
  const [proviso, setProviso] = useState<string | null>(null);
  const [preview, setPreview] = useState<PrintResponse | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const idem = useRef(newIdempotencyKey());
  // 発行した回数。発行より前に予約・開始したプレビューの取り直し（戻ると「再発行」付き）で、発行した領収書の表示を上書きしない
  const issuedCount = useRef(0);

  // 入力に合わせてプレビューを作り直す（記録はしない）
  useEffect(() => {
    let stop = false;
    const at = issuedCount.current;
    const t = setTimeout(async () => {
      try {
        const qs = new URLSearchParams({ addressee, proviso: proviso ?? "" });
        const r = await api<PrintResponse>(`/api/checkouts/${checkoutId}/print/ryoshusho?${qs}`);
        if (stop || at !== issuedCount.current) return;
        setPreview(r);
        setLoadError(null);
        if (proviso === null) setProviso(r.proviso_default ?? "");
      } catch (e) {
        if (!stop) setLoadError(e);
      }
    }, 300);
    return () => {
      stop = true;
      clearTimeout(t);
    };
  }, [checkoutId, addressee, proviso]);

  useEffect(() => {
    idem.current = newIdempotencyKey();
  }, [addressee, proviso]);

  async function issue() {
    setBusy(true);
    setError(null);
    issuedCount.current++;
    try {
      const r = await api<PrintResponse>(`/api/checkouts/${checkoutId}/print/ryoshusho`, {
        method: "POST",
        idempotencyKey: idem.current,
        body: { addressee, proviso: proviso ?? "" },
      });
      setPreview(r);
      setDone(true);
      sendToPrinter(r.epos_xml);
    } catch (e) {
      setError(errorMessage(e));
      if (!(e instanceof ApiError && (e.code === "network" || e.code === "in_progress"))) idem.current = newIdempotencyKey();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="領収書"
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            閉じる
          </button>
          {preview?.printable ? (
            <button className="btn btn-xl btn-primary" onClick={issue} disabled={busy || !preview || proviso === null} data-testid="ryoshusho-issue">
              🖨 {preview.issued_before || done ? "再発行して印刷" : "発行して印刷"}
            </button>
          ) : (
            <PrintButton resp={preview} />
          )}
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-3">
          <Field label="宛名（空欄なら手書き用に空けます）">
            <input className="input" value={addressee} onChange={(e) => setAddressee(e.target.value)} maxLength={40} placeholder="例: 株式会社〇〇" />
          </Field>
          <Field label="但し書き">
            <input className="input" value={proviso ?? ""} onChange={(e) => setProviso(e.target.value)} maxLength={40} />
          </Field>
          {preview?.issued_before && !done && (
            <p className="text-base font-bold" style={{ color: "var(--color-warn)" }}>
              この会計の領収書は発行済みです。もう一度出すと「再発行」と印字されます。
            </p>
          )}
          {done && (
            <p className="text-base font-bold" style={{ color: "var(--color-ok)" }}>
              発行を記録しました。エプソンのアプリで印刷します（終わったら画面左上の「◀」で戻ります）。
            </p>
          )}
          {error && (
            <p className="text-base font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </p>
          )}
        </div>
        <div>
          <ErrorBox error={loadError} />
          {preview ? <SlipPreview slip={preview.slip} /> : !loadError && <Spinner label="領収書を作っています…" />}
        </div>
      </div>
    </Modal>
  );
}

/** 会計のあとに出す「レシート」「領収書」ボタン（プリンターで出す設定のものだけ） */
export function CheckoutPrintButtons({ checkoutId, className = "btn btn-xl" }: { checkoutId: number; className?: string }) {
  const printer = usePrinter();
  const [open, setOpen] = useState<"receipt" | "ryoshusho" | null>(null);
  if (!printer.can("receipt") && !printer.can("ryoshusho")) return null;
  return (
    <>
      {printer.can("receipt") && (
        <button className={className} onClick={() => setOpen("receipt")} data-testid="print-receipt">
          🖨 レシート
        </button>
      )}
      {printer.can("ryoshusho") && (
        <button className={className} onClick={() => setOpen("ryoshusho")} data-testid="print-ryoshusho">
          🖨 領収書
        </button>
      )}
      {open === "receipt" && <PrintDialog title="会計伝票（レシート）" path={`/api/checkouts/${checkoutId}/print/receipt`} onClose={() => setOpen(null)} />}
      {open === "ryoshusho" && <RyoshushoDialog checkoutId={checkoutId} onClose={() => setOpen(null)} />}
    </>
  );
}
