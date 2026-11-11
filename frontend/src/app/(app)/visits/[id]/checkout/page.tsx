"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFloor } from "@/components/AppShell";
import { TerminalAttentionList, TerminalWaitDialog, rememberTerminal, rememberedTerminal } from "@/components/TerminalPay";
import { useToast } from "@/components/Toast";
import { ErrorBox, Modal, NumberPad, Spinner } from "@/components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { LINE_TYPE_LABELS, toInt, yen } from "@/lib/format";
import type { Checkout, Quote, StoreSettings, TerminalPayment, TerminalStatusInfo, Visit } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Method = "cash" | "card" | "mixed" | "tsuke";
type Field = "tendered" | "card";

/** お預かりのよくある金額（ちょうど・千円単位・5千円単位・1万円単位…） */
function quickTendered(total: number): number[] {
  if (total <= 0) return [];
  const ceil = (unit: number) => Math.ceil(total / unit) * unit;
  const set = new Set<number>([total, ceil(1000), ceil(5000), ceil(10000), ceil(10000) + 10000]);
  return [...set].filter((v) => v >= total).sort((a, b) => a - b).slice(0, 5);
}

export default function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { reload: reloadFloor } = useFloor();
  const visitQ = useLoad<Visit>(`/api/visits/${id}`);
  const settings = useLoad<StoreSettings>("/api/store/settings");
  const [split, setSplit] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<unknown>(null);
  const [method, setMethod] = useState<Method>("cash");
  const [tendered, setTendered] = useState("");
  const [card, setCard] = useState("");
  const [field, setField] = useState<Field>("tendered");
  const [cardRef, setCardRef] = useState("");
  const [debtor, setDebtor] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Checkout | null>(null);
  const idem = useRef(newIdempotencyKey());
  // 決済端末（カード）。端末が使えない・手入力を選んだときは、これまで通り端末に手で金額を入れて記録する
  const term = useLoad<TerminalStatusInfo>("/api/terminals/status");
  const [tp, setTp] = useState<TerminalPayment | null>(null);
  const [attention, setAttention] = useState<TerminalPayment[]>([]);
  const [terminalId, setTerminalId] = useState<number | null>(null);
  const [manualCard, setManualCard] = useState(false);

  const visit = visitQ.data;
  const openLines = useMemo(() => (visit?.lines ?? []).filter((l) => l.status === "active" && l.checkout === null), [visit]);
  const lineIds = split ? [...selected] : null;

  const loadQuote = useCallback(async () => {
    if (!visit) return;
    if (split && selected.size === 0) {
      setQuote(null);
      return;
    }
    try {
      setQuote(await api<Quote>(`/api/visits/${id}/quote`, { method: "POST", body: { line_ids: lineIds } }));
      setQuoteError(null);
    } catch (e) {
      setQuoteError(e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, visit, split, selected]);

  useEffect(() => {
    loadQuote();
  }, [loadQuote]);

  // 送信中の端末決済があれば続きから表示する（画面を開き直した・別の端末から開いたとき）
  const loadTerminalState = useCallback(async () => {
    try {
      const r = await api<{ active: TerminalPayment | null; needs_attention: TerminalPayment[] }>(`/api/visits/${id}/terminal-payments`);
      if (r.active) {
        // 送ったときの支払い方法と金額に画面を戻す（キャンセル・拒否のあと、そのまま送り直せるように）
        const a = r.active;
        if (Number(a.cash_amount) > 0) {
          setMethod("mixed");
          setCard(String(toInt(a.amount)));
          setTendered(String(toInt(a.cash_tendered)));
          setField("card");
        } else {
          setMethod("card");
        }
        setTp(a);
      }
      setAttention(r.needs_attention);
    } catch {
      /* 端末連携が使えなくても会計はできる */
    }
  }, [id]);
  useEffect(() => {
    loadTerminalState();
  }, [loadTerminalState]);
  useEffect(() => {
    const ids = term.data?.terminals.map((t) => t.id) ?? [];
    setTerminalId((cur) => (cur !== null && ids.includes(cur) ? cur : rememberedTerminal(ids)));
  }, [term.data]);

  const total = toInt(quote?.total);
  const tenderedN = toInt(tendered);
  const cardN = toInt(card);
  const cashPart = method === "cash" ? total : method === "mixed" ? Math.max(0, total - cardN) : 0;
  const change = Math.max(0, tenderedN - cashPart);
  const short = Math.max(0, cashPart - tenderedN);

  // 支払い内容が変わったら新しい操作キーにする（同じ内容の再送は二重にならない）
  useEffect(() => {
    idem.current = newIdempotencyKey();
    setError(null);
  }, [method, tendered, card, total, split, selected]);

  const terminalOn = !!term.data?.enabled && terminalId !== null && !manualCard && (method === "card" || method === "mixed");
  const ready =
    total > 0 &&
    ((method === "cash" && tendered !== "" && tenderedN >= total) ||
      method === "card" ||
      (method === "mixed" && cardN > 0 && cardN < total && tendered !== "" && tenderedN >= cashPart) ||
      method === "tsuke");

  function payments() {
    if (method === "cash") return [{ method: "cash", amount: total, tendered: tenderedN }];
    if (method === "card") return [{ method: "card", amount: total, card_reference: cardRef }];
    if (method === "mixed")
      return [
        { method: "card", amount: cardN, card_reference: cardRef },
        { method: "cash", amount: cashPart, tendered: tenderedN },
      ];
    return [{ method: "receivable", amount: total, debtor_name: debtor || visit?.customer_display || "" }];
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const c = await api<Checkout>(`/api/visits/${id}/checkouts`, { method: "POST", idempotencyKey: idem.current, body: { line_ids: lineIds, payments: payments() } });
      setResult(c);
      setConfirmOpen(false);
      reloadFloor();
    } catch (e) {
      if (e instanceof ApiError && (e.code === "network" || e.code === "in_progress")) {
        // 保存結果不明: 同じ操作キーのまま。もう一度押しても二重会計にはならない
        setError(`${e.message} もう一度「確定する」を押しても二重には会計されません。`);
      } else {
        setError(errorMessage(e));
        idem.current = newIdempotencyKey();
      }
    } finally {
      setBusy(false);
    }
  }

  async function sendToTerminal() {
    if (terminalId === null) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<TerminalPayment>(`/api/visits/${id}/terminal-payments`, {
        method: "POST",
        idempotencyKey: idem.current,
        body: {
          terminal_id: terminalId,
          line_ids: lineIds,
          card_amount: method === "card" ? total : cardN,
          cash_amount: method === "mixed" ? cashPart : 0,
          cash_tendered: method === "mixed" ? tenderedN : null,
        },
      });
      rememberTerminal(terminalId);
      setTp(r);
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      if (code === "terminal_busy") await loadTerminalState();
      if (code === "amount_changed") loadQuote();
      if (code === "network" || code === "in_progress") {
        setError(`${errorMessage(e)} もう一度押しても端末に二重には送られません。`);
      } else {
        setError(errorMessage(e));
        idem.current = newIdempotencyKey();
      }
    } finally {
      setBusy(false);
    }
  }

  const onTerminalCompleted = useCallback(
    async (t: TerminalPayment) => {
      reloadFloor();
      if (!t.checkout) return;
      try {
        setResult(await api<Checkout>(`/api/checkouts/${t.checkout}`));
      } catch (e) {
        setError(errorMessage(e));
      }
      setTp(null);
    },
    [reloadFloor],
  );

  function press(v: string) {
    if (method === "mixed" && field === "card") setCard(v);
    else setTendered(v);
  }

  // キーボード（PC）: Enter＝確認画面を開く／確認画面で Enter＝確定、Tab＝現金＋カードで入力欄を切り替え
  const keyState = useRef({ ready, busy, confirmOpen, method, result, terminalOn });
  keyState.current = { ready, busy, confirmOpen, method, result, terminalOn };
  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;
  const sendRef = useRef(sendToTerminal);
  sendRef.current = sendToTerminal;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = keyState.current;
      if (k.result || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      if (e.key === "Enter" && !typing) {
        e.preventDefault();
        if (k.confirmOpen) {
          if (!k.busy) confirmRef.current();
        } else if (k.ready && !k.busy && !document.querySelector('[role="dialog"]')) {
          if (k.terminalOn) sendRef.current();
          else setConfirmOpen(true);
        }
      } else if (e.key === "Tab" && k.method === "mixed" && !k.confirmOpen && !typing) {
        e.preventDefault();
        setField((f) => (f === "card" ? "tendered" : "card"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (visitQ.loading || !visit) return visitQ.error ? <ErrorBox error={visitQ.error} onRetry={() => visitQ.reload(false)} /> : <Spinner />;

  // --- 会計完了 ---
  if (result) {
    const cash = result.payments.find((p) => p.method === "cash");
    const cardP = result.payments.find((p) => p.method === "card");
    const rec = result.payments.find((p) => p.method === "receivable");
    const remaining = visit.lines.filter((l) => l.status === "active" && l.checkout === null).length - result.lines.length;
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="card text-center space-y-2" style={{ borderColor: "var(--color-ok)", borderWidth: 2 }}>
          <div className="text-2xl font-bold">
            {visit.table_name} お会計完了（会計#{result.number}）
          </div>
          {cash ? (
            <>
              <div className="text-2xl" style={{ color: "var(--color-muted)" }}>
                お釣り
              </div>
              <div className="font-bold num" style={{ fontSize: "5.5rem", lineHeight: 1, color: "var(--color-ok)" }}>
                {yen(cash.change_amount)}
              </div>
            </>
          ) : (
            <div className="text-3xl font-bold" style={{ color: "var(--color-ok)" }}>
              お釣りはありません
            </div>
          )}
          <div className="text-xl num space-y-1 pt-2">
            <div>
              ご請求 <strong>{yen(result.total)}</strong>
            </div>
            {cash && (
              <div>
                現金 {yen(cash.amount)}（お預かり {yen(cash.tendered)}）
              </div>
            )}
            {cardP && <div>カード {yen(cardP.amount)}</div>}
            {rec && <div style={{ color: "var(--color-warn)" }}>ツケ（未収） {yen(rec.amount)}</div>}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Link href={`/checkouts/${result.id}/handwrite`} className="btn btn-xl">
            手書き領収書の金額
          </Link>
          {remaining > 0 ? (
            <button
              className="btn btn-xl btn-primary"
              onClick={() => {
                setResult(null);
                setSelected(new Set());
                setSplit(false);
                setTendered("");
                setCard("");
                visitQ.reload(false);
              }}
            >
              残りの明細を会計する
            </button>
          ) : (
            <button
              className="btn btn-xl btn-primary"
              onClick={() => {
                toast.show({ message: `${visit.table_name} 会計しました（${yen(result.total)}）` });
                router.replace("/floor");
              }}
            >
              卓一覧へ
            </button>
          )}
        </div>
      </div>
    );
  }

  if (visit.status !== "open" || openLines.length === 0) {
    return (
      <div className="max-w-2xl mx-auto space-y-4">
        <h1 className="text-3xl font-bold">{visit.table_name} のお会計</h1>
        <div className="card space-y-3">
          <p className="text-xl">未会計の明細がありません。</p>
          <button className="btn btn-xl" onClick={() => router.push("/floor")}>
            卓一覧へ
          </button>
        </div>
      </div>
    );
  }

  const receivableAllowed = settings.data?.receivable_allowed ?? false;
  const tabs: { key: Method; label: string }[] = [
    { key: "cash", label: "現金" },
    { key: "card", label: "カード" },
    { key: "mixed", label: "現金＋カード" },
    ...(receivableAllowed ? [{ key: "tsuke" as Method, label: "ツケ" }] : []),
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-xl" onClick={() => router.push(`/visits/${visit.id}`)}>
          ← 伝票へ
        </button>
        <h1 className="text-3xl font-bold">{visit.table_name} のお会計</h1>
        <span className="text-lg" style={{ color: "var(--color-muted)" }}>
          伝票#{visit.slip_number} ・ {visit.guest_count}名
        </span>
      </div>
      <ErrorBox error={quoteError} onRetry={loadQuote} />
      <TerminalAttentionList items={attention} onResolved={loadTerminalState} />

      <div className="grid grid-cols-12 gap-3">
        {/* 左: 請求額 */}
        <div className="col-span-12 lg:col-span-5 space-y-3">
          <section className="card space-y-2">
            <div className="text-xl" style={{ color: "var(--color-muted)" }}>
              ご請求額（税込）{split && " ・ 選んだ明細のみ"}
            </div>
            <div className="font-bold num" style={{ fontSize: "4rem", lineHeight: 1.05 }}>
              {quote ? yen(quote.total) : split && selected.size === 0 ? <span className="text-2xl">下で明細を選んでください</span> : <span className="text-2xl">計算中…</span>}
            </div>
            {quote && (
              <div className="text-lg num space-y-0.5 pt-2" style={{ color: "var(--color-muted)" }}>
                {Object.entries(quote.breakdown)
                  .filter(([k]) => k !== "discount")
                  .map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span>{LINE_TYPE_LABELS[k] ?? k}</span>
                      <span>{yen(v)}</span>
                    </div>
                  ))}
                {Number(quote.discount_total) > 0 && (
                  <div className="flex justify-between">
                    <span>値引き</span>
                    <span>−{yen(quote.discount_total)}</span>
                  </div>
                )}
                {Number(quote.service_charge) > 0 && (
                  <div className="flex justify-between">
                    <span>サービス料</span>
                    <span>{yen(Number(quote.service_charge) + Number(quote.service_charge_tax))}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span>うち消費税</span>
                  <span>{yen(quote.tax_total)}</span>
                </div>
                {Number(quote.rounding_adjustment) !== 0 && (
                  <div className="flex justify-between">
                    <span>端数調整</span>
                    <span>{yen(quote.rounding_adjustment, { sign: true })}</span>
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="card space-y-2">
            <button
              className={`btn btn-lg w-full ${split ? "btn-primary" : ""}`}
              onClick={() => {
                setSplit((v) => !v);
                setSelected(new Set());
              }}
            >
              {split ? "✓ 一部だけ会計（明細を選ぶ）" : "一部だけ会計する（分割・個別）"}
            </button>
            {split && (
              <div className="space-y-1 max-h-[36vh] overflow-y-auto">
                {openLines.map((l) => {
                  const on = selected.has(l.id);
                  return (
                    <button
                      key={l.id}
                      className="w-full flex items-center gap-3 rounded-xl px-3 py-3 text-left"
                      style={{ background: on ? "#17223a" : "var(--color-panel2)", border: `2px solid ${on ? "var(--color-accent)" : "var(--color-border)"}` }}
                      onClick={() => {
                        const next = new Set(selected);
                        if (on) next.delete(l.id);
                        else next.add(l.id);
                        setSelected(next);
                      }}
                      aria-pressed={on}
                    >
                      <span className="text-2xl w-8">{on ? "☑" : "☐"}</span>
                      <span className="flex-1 text-lg">
                        {l.name}
                        {Number(l.quantity) !== 1 && ` ×${Number(l.quantity)}`}
                      </span>
                      <span className="text-lg font-bold num">{yen(l.amount)}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        {/* 右: 支払い */}
        <div className="col-span-12 lg:col-span-7 space-y-3">
          <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}>
            {tabs.map((t) => (
              <button
                key={t.key}
                className={`btn btn-xl whitespace-nowrap ${method === t.key ? "btn-primary" : ""}`}
                style={{ fontSize: t.label.length > 4 ? "1.15rem" : undefined, padding: "0 0.5rem" }}
                onClick={() => {
                  setMethod(t.key);
                  setField(t.key === "mixed" ? "card" : "tendered");
                }}
                aria-pressed={method === t.key}
              >
                {t.label}
              </button>
            ))}
          </div>

          <section className="card space-y-3">
            {method === "card" && terminalOn && (
              <div className="space-y-3">
                <p className="text-2xl font-bold">
                  「決済端末に送る」を押すと、端末に <span className="num" style={{ color: "var(--color-warn)" }}>{yen(total)}</span> が表示されます
                </p>
                <p className="text-base" style={{ color: "var(--color-muted)" }}>
                  お客様に端末でカードを読み取ってもらうと、自動で会計まで終わります。カード番号は記録しません。
                </p>
              </div>
            )}
            {method === "card" && !terminalOn && (
              <div className="space-y-3">
                <p className="text-2xl font-bold">
                  決済端末に <span className="num" style={{ color: "var(--color-warn)" }}>{yen(total)}</span> を入力して決済してください
                </p>
                <p className="text-base" style={{ color: "var(--color-muted)" }}>
                  アプリからカード決済はしません。端末で「決済完了」を確認してから下のボタンを押してください。カード番号は記録しません。
                </p>
                <input className="input" value={cardRef} onChange={(e) => setCardRef(e.target.value)} placeholder="端末の伝票番号（任意）" />
              </div>
            )}

            {method === "tsuke" && (
              <div className="space-y-3">
                <p className="text-2xl font-bold">
                  全額 <span className="num" style={{ color: "var(--color-warn)" }}>{yen(total)}</span> をツケ（未収）にします
                </p>
                <input className="input" value={debtor} onChange={(e) => setDebtor(e.target.value)} placeholder={visit.customer_display || "お客様の名前"} />
                <p className="text-base" style={{ color: "var(--color-muted)" }}>
                  回収したときは「売上・会計履歴」の未収から記録します（売上に二重に入りません）。
                </p>
              </div>
            )}

            {(method === "cash" || method === "mixed") && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-3">
                  {method === "mixed" && (
                    <>
                      <BigField label="カードで払う金額" value={card} active={field === "card"} onFocus={() => setField("card")} />
                      <div className="flex justify-between text-xl num">
                        <span>現金で払う分</span>
                        <strong>{yen(cashPart)}</strong>
                      </div>
                    </>
                  )}
                  <BigField label={method === "mixed" ? "お預かり（現金）" : "お預かり"} value={tendered} active={field === "tendered"} onFocus={() => setField("tendered")} />
                  {(method === "cash" || cardN > 0) && field === "tendered" && (
                    <div className="grid grid-cols-2 gap-2">
                      {quickTendered(cashPart).map((v) => (
                        <button key={v} className="btn btn-lg num" onClick={() => setTendered(String(v))}>
                          {v === cashPart ? `ちょうど ${yen(v)}` : yen(v)}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="card-2 text-center">
                    {tendered === "" ? (
                      <div className="text-xl" style={{ color: "var(--color-muted)" }}>
                        お預かりした金額を入れてください
                      </div>
                    ) : short > 0 ? (
                      <div className="text-3xl font-bold num" style={{ color: "var(--color-danger)" }}>
                        あと {yen(short)} 足りません
                      </div>
                    ) : (
                      <>
                        <div className="text-xl" style={{ color: "var(--color-muted)" }}>
                          お釣り
                        </div>
                        <div className="font-bold num" style={{ fontSize: "3.6rem", lineHeight: 1.05, color: "var(--color-ok)" }}>
                          {yen(change)}
                        </div>
                      </>
                    )}
                  </div>
                </div>
                <div>
                  <NumberPad value={method === "mixed" && field === "card" ? card : tendered} onChange={press} />
                </div>
              </div>
            )}

            {method === "mixed" && cardN > 0 && (
              <p className="text-base" style={{ color: "var(--color-muted)" }}>
                {terminalOn
                  ? `カード分 ${yen(cardN)} を決済端末に送ります。現金はお預かりして、端末の決済が終わったらお釣りを渡してください。`
                  : `カード分 ${yen(cardN)} は決済端末で決済してから確定してください。`}
              </p>
            )}

            {terminalOn && (term.data?.terminals.length ?? 0) > 1 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base" style={{ color: "var(--color-muted)" }}>
                  送る端末
                </span>
                {term.data?.terminals.map((t) => (
                  <button key={t.id} className={`btn btn-lg ${terminalId === t.id ? "btn-primary" : ""}`} onClick={() => setTerminalId(t.id)} aria-pressed={terminalId === t.id}>
                    {t.name}
                  </button>
                ))}
              </div>
            )}

            {error && (
              <p className="text-xl font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {error}
              </p>
            )}

            <p className="kbd-hint text-sm" style={{ color: "var(--color-muted)" }}>
              キーボード: {method === "cash" || method === "mixed" ? "数字で金額を入力 ・ " : ""}
              {terminalOn ? "Enter で決済端末に送る" : "Enter で会計（確認画面でもう一度 Enter で確定）"}
              {method === "mixed" ? " ・ Tab でカード／お預かりの欄を切り替え" : ""}
            </p>
            {terminalOn ? (
              <button className="btn btn-xl btn-primary w-full" disabled={!ready || busy} onClick={sendToTerminal}>
                {busy
                  ? "端末に送っています…"
                  : method === "mixed" && (cardN <= 0 || cardN >= total)
                    ? "カードの金額を入れてください"
                    : method === "mixed" && (short > 0 || tendered === "")
                      ? "お預かりを入れてください"
                      : `決済端末に送る（カード ${yen(method === "card" ? total : cardN)}）`}
              </button>
            ) : (
            <button className="btn btn-xl btn-ok w-full" disabled={!ready || busy} onClick={() => setConfirmOpen(true)}>
              {method === "cash" && (tendered === "" ? "お預かりを入れてください" : short > 0 ? "金額が足りません" : `会計する（お釣り ${yen(change)}）`)}
              {method === "card" && "端末で決済できた → 会計する"}
              {method === "mixed" && (cardN <= 0 || cardN >= total ? "カードの金額を入れてください" : short > 0 || tendered === "" ? "お預かりを入れてください" : `会計する（お釣り ${yen(change)}）`)}
              {method === "tsuke" && "ツケで会計する"}
            </button>
            )}
            {!!term.data?.enabled && (method === "card" || method === "mixed") && (
              <button className="btn btn-sm btn-ghost w-full" onClick={() => setManualCard((v) => !v)}>
                {manualCard ? "← 決済端末に送る方法に戻す" : "端末に金額を手で入れて決済した場合はこちら（手入力で記録）"}
              </button>
            )}
          </section>
        </div>
      </div>

      {tp && (
        <TerminalWaitDialog
          key={tp.id}
          initial={tp}
          onCompleted={onTerminalCompleted}
          onClose={() => {
            setTp(null);
            idem.current = newIdempotencyKey();
            loadTerminalState();
          }}
        />
      )}

      {confirmOpen && (
        <Modal
          title="この内容で会計しますか？"
          onClose={busy ? undefined : () => setConfirmOpen(false)}
          footer={
            <>
              <button className="btn btn-xl" onClick={() => setConfirmOpen(false)} disabled={busy}>
                戻る
              </button>
              <button className="btn btn-xl btn-ok" onClick={confirm} disabled={busy} style={{ minWidth: 220 }}>
                {busy ? "処理中…" : "確定する"}
              </button>
            </>
          }
        >
          <div className="space-y-2 text-2xl num">
            <div className="flex justify-between font-bold">
              <span>ご請求</span>
              <span>{yen(total)}</span>
            </div>
            {method === "card" && (
              <div className="flex justify-between">
                <span>カード</span>
                <span>{yen(total)}</span>
              </div>
            )}
            {method === "mixed" && (
              <div className="flex justify-between">
                <span>カード</span>
                <span>{yen(cardN)}</span>
              </div>
            )}
            {(method === "cash" || method === "mixed") && (
              <>
                <div className="flex justify-between">
                  <span>現金（お預かり {yen(tenderedN)}）</span>
                  <span>{yen(cashPart)}</span>
                </div>
                <div className="flex justify-between font-bold" style={{ color: "var(--color-ok)" }}>
                  <span>お釣り</span>
                  <span>{yen(change)}</span>
                </div>
              </>
            )}
            {method === "tsuke" && (
              <div className="flex justify-between" style={{ color: "var(--color-warn)" }}>
                <span>ツケ（未収）</span>
                <span>{yen(total)}</span>
              </div>
            )}
            {split && <div className="text-lg" style={{ color: "var(--color-muted)" }}>選んだ {selected.size} 件の明細だけを会計します。</div>}
            {error && (
              <p className="text-xl font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
                {error}
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

function BigField({ label, value, active, onFocus }: { label: string; value: string; active: boolean; onFocus: () => void }) {
  return (
    <button
      type="button"
      onClick={onFocus}
      className="w-full rounded-2xl px-4 py-3 text-left"
      style={{ background: "var(--color-panel2)", border: `3px solid ${active ? "var(--color-accent)" : "var(--color-border)"}` }}
      aria-pressed={active}
    >
      <div className="text-lg" style={{ color: "var(--color-muted)" }}>
        {label}
        {active && " ← 入力中"}
      </div>
      <div className="text-5xl font-bold num text-right">{value ? yen(value) : "¥0"}</div>
    </button>
  );
}
