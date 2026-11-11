"use client";

/**
 * 領収書の登録（店舗タブレット）: 1/4 撮影 → 2/4 確認 → 3/4 支払方法 → 4/4 登録。
 * 勘定科目・税区分はスタッフに判断させない。登録後は卓一覧へ戻り「取り消す」付きの通知を出す。
 */
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { CameraCapture, type CapturedFile } from "@/components/CameraCapture";
import { FieldErrors, ReasonPicker, RequireCaps, Wizard } from "@/components/common";
import { PollingDialog } from "@/components/PollingDialog";
import { useToast } from "@/components/Toast";
import { ErrorBox, NumberPad } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey, uploadFile } from "@/lib/api";
import { fmtDate, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import { PAYMENT_METHODS, type Receipt, type ReceiptPaymentMethod } from "@/lib/types_expenses";

const STEPS = ["撮影", "確認", "支払方法", "登録"];
const MEMO_OPTIONS = ["氷・レモン・割り物", "お菓子・乾き物", "備品・消耗品", "お酒の仕入", "タクシー・交通費", "お客様用の買い物"];

function todayJst(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}
function addDays(iso: string, d: number): string {
  const dt = new Date(iso + "T00:00:00+09:00");
  dt.setDate(dt.getDate() + d);
  return dt.toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}

export default function NewReceiptPage() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const [step, setStep] = useState(0);
  const [captured, setCaptured] = useState<CapturedFile | null>(null);
  const [uploadKey, setUploadKey] = useState(newIdempotencyKey());
  const [progress, setProgress] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [polling, setPolling] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // 確認値
  const [date, setDate] = useState(todayJst());
  const [amount, setAmount] = useState("");
  const [vendor, setVendor] = useState("");
  const [method, setMethod] = useState<ReceiptPaymentMethod>("store_cash");
  const [memo, setMemo] = useState("");

  // 撮り直したら新しい登録として扱う（二重登録防止キーも変える）
  useEffect(() => {
    if (!captured) setUploadKey(newIdempotencyKey());
  }, [captured]);

  function fillFrom(r: Receipt) {
    setReceipt(r);
    if (r.receipt_date) setDate(r.receipt_date);
    if (r.total_amount) setAmount(String(Math.round(Number(r.total_amount))));
    if (r.vendor) setVendor(r.vendor);
  }

  async function upload() {
    if (!captured) return;
    setBusy(true);
    setErr(null);
    setProgress(0);
    try {
      const r = await uploadFile<Receipt>("/api/expenses/receipts", captured.file, { payment_method: method, origin: captured.isPdf ? "electronic" : "paper_photo", captured_at: new Date().toISOString() },
        { idempotencyKey: uploadKey, onProgress: setProgress });
      fillFrom(r);
      if (r.status === "ocr_running") setPolling(true);
      else setStep(1);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  async function finish() {
    if (!receipt) return;
    setBusy(true);
    setErr(null);
    try {
      const r1 = await api<Receipt>(`/api/expenses/receipts/${receipt.id}`, {
        method: "PATCH",
        body: { receipt_date: date, total_amount: amount, vendor, payment_method: method, memo, version: receipt.version },
      });
      const r2 = await api<Receipt>(`/api/expenses/receipts/${receipt.id}/submit`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { version: r1.version } });
      toast.show({
        message: `領収書を登録しました（${yen(r2.total_amount)}・確認待ち）`,
        action: {
          label: "取り消す",
          onClick: async () => {
            await api(`/api/expenses/receipts/${r2.id}/void`, { method: "POST", idempotencyKey: newIdempotencyKey(), body: { reason: "登録直後の取り消し" } });
            toast.show({ message: "登録を取り消しました", tone: "info" });
          },
        },
      });
      router.replace(can("floor.operate") ? "/floor" : "/receipts");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const ocr = receipt?.ocr;
  const missing = useMemo(() => new Set(ocr?.missing_fields ?? []), [ocr]);
  const breakdownTotal = (receipt?.tax_breakdown ?? []).reduce((s, r) => s + Number(r.taxable_incl), 0);
  const mismatch = receipt && receipt.tax_breakdown.length > 0 && amount !== "" && breakdownTotal !== Number(amount);

  return (
    <RequireCaps caps={["expenses.submit"]} message="領収書を登録する権限がありません。">
      <div className="max-w-3xl mx-auto space-y-4">
        <Wizard steps={STEPS} current={step} />
        <ErrorBox error={err} />
        {err instanceof ApiError && err.details ? <FieldErrors details={err.details} /> : null}

        {step === 0 && (
          <div className="space-y-3">
            <h1 className="text-2xl font-bold">領収書を撮る</h1>
            <CameraCapture value={captured} onChange={setCaptured} />
            {captured && (
              <button className="btn btn-xl btn-primary w-full" style={{ minHeight: 96 }} disabled={busy} onClick={upload}>
                {busy ? (progress !== null ? `送信中… ${Math.round(progress * 100)}%` : "送信中…") : "これでよい →"}
              </button>
            )}
          </div>
        )}

        {polling && receipt && (
          <PollingDialog<Receipt>
            title="領収書を読み取っています"
            path={`/api/expenses/receipts/${receipt.id}`}
            isDone={(r) => r.status !== "ocr_running"}
            onDone={(r) => {
              setPolling(false);
              fillFrom(r);
              setStep(1);
            }}
            onFailed={async () => {
              setPolling(false);
              setStep(1);
            }}
            message="金額・日付・お店の名前を読み取っています。少しお待ちください…"
          />
        )}

        {step === 1 && receipt && (
          <div className="space-y-4">
            <h1 className="text-2xl font-bold">内容を確認してください</h1>
            {ocr?.status === "failed" || ocr?.status === "skipped" ? (
              <p className="badge badge-warn" style={{ fontSize: "1rem" }}>
                読み取れなかったので、手で入力してください。
              </p>
            ) : null}
            {ocr?.warnings.map((w) => (
              <p key={w.code} className="badge badge-warn" style={{ fontSize: "1rem" }}>
                {w.message}
              </p>
            ))}
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-3">
                <div>
                  <span className="label">金額（税込）</span>
                  <div className="card-2 num text-right" style={{ fontSize: "3rem", fontWeight: 800, minHeight: 84, borderColor: missing.has("total_amount") && amount === "" ? "var(--color-danger)" : undefined }}>
                    {amount ? yen(amount) : "—"}
                  </div>
                  {mismatch && (
                    <p className="text-sm mt-1" style={{ color: "var(--color-warn)" }}>
                      8%・10% の内訳の合計（{yen(breakdownTotal)}）と違います。登録はできますが、承認のときに確認されます。
                    </p>
                  )}
                </div>
                <NumberPad value={amount} onChange={setAmount} quick={[1000, 2000, 3000, 5000]} />
              </div>
              <div className="space-y-3">
                <div>
                  <span className="label">日付</span>
                  <div className="grid grid-cols-2 gap-2 mb-2">
                    <button className={`btn btn-lg ${date === todayJst() ? "btn-primary" : ""}`} onClick={() => setDate(todayJst())}>
                      今日
                    </button>
                    <button className={`btn btn-lg ${date === addDays(todayJst(), -1) ? "btn-primary" : ""}`} onClick={() => setDate(addDays(todayJst(), -1))}>
                      昨日
                    </button>
                  </div>
                  <input type="date" className="input input-lg" style={{ textAlign: "left" }} max={todayJst()} value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
                  <div className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
                    {fmtDate(date)}
                  </div>
                </div>
                <div>
                  <span className="label">お店の名前（支払先）</span>
                  <input className="input input-lg" style={{ textAlign: "left", borderColor: missing.has("vendor") && !vendor ? "var(--color-danger)" : undefined }} value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="読み取れなかったら入力" />
                </div>
                {receipt.tax_breakdown.length > 0 && (
                  <div className="card-2">
                    <div className="label">税率の内訳（読み取り）</div>
                    {receipt.tax_breakdown.map((r, i) => (
                      <div key={i} className="flex justify-between text-lg num">
                        <span>{r.rate}% 対象</span>
                        <span>{yen(r.taxable_incl)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {receipt.invoice_number && (
                  <div className="text-sm" style={{ color: "var(--color-muted)" }}>
                    登録番号 {receipt.invoice_number}
                  </div>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button className="btn btn-xl" onClick={() => { setStep(0); setCaptured(null); setReceipt(null); }}>
                撮り直す
              </button>
              <button className="btn btn-xl btn-primary" disabled={!amount || Number(amount) <= 0 || !date} onClick={() => setStep(2)}>
                次へ →
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <h1 className="text-2xl font-bold">何で払いましたか</h1>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {PAYMENT_METHODS.map((m) => (
                <button key={m.key} className={`btn btn-xl ${method === m.key ? "btn-primary" : ""}`} style={{ minHeight: 96, flexDirection: "column", alignItems: "flex-start" }} onClick={() => setMethod(m.key)}>
                  <span>{m.label}</span>
                  <span className="text-sm font-normal" style={{ opacity: 0.8 }}>
                    {m.hint}
                  </span>
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button className="btn btn-xl" onClick={() => setStep(1)}>
                ← 戻る
              </button>
              <button className="btn btn-xl btn-primary" onClick={() => setStep(3)}>
                次へ →
              </button>
            </div>
          </div>
        )}

        {step === 3 && receipt && (
          <div className="space-y-4">
            <h1 className="text-2xl font-bold">メモ（任意）と登録</h1>
            <ReasonPicker options={MEMO_OPTIONS} value={memo} onChange={setMemo} otherLabel="その他・入力する" />
            <div className="card-2 space-y-1 text-lg">
              <div className="flex justify-between">
                <span>金額</span>
                <strong className="num">{yen(amount)}</strong>
              </div>
              <div className="flex justify-between">
                <span>日付</span>
                <span>{fmtDate(date)}</span>
              </div>
              <div className="flex justify-between">
                <span>支払先</span>
                <span>{vendor || "—"}</span>
              </div>
              <div className="flex justify-between">
                <span>支払方法</span>
                <span>{PAYMENT_METHODS.find((m) => m.key === method)?.label}</span>
              </div>
              {method === "store_cash" && (
                <p className="text-sm" style={{ color: "var(--color-muted)" }}>
                  承認されるとレジの現金経費として扱われます（日締めの「現金経費」と同じ）。
                </p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button className="btn btn-xl" onClick={() => setStep(2)} disabled={busy}>
                ← 戻る
              </button>
              <button className="btn btn-xl btn-ok" style={{ minHeight: 100, fontSize: "1.8rem" }} disabled={busy} onClick={finish}>
                {busy ? "登録中…" : "登録する"}
              </button>
            </div>
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              {errorMessage(err) !== "不明なエラーが発生しました。" && err ? null : "登録後はオーナー・経理が確認します。間違いがあれば差し戻されます。"}
            </p>
          </div>
        )}
      </div>
    </RequireCaps>
  );
}
