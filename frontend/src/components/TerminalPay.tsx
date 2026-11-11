"use client";

/**
 * 決済端末での会計（PC・iPad 共通）。
 * 金額は NOXIA のサーバーから決済会社経由で端末に届くので、どの画面から送っても同じ動きになる。
 * カード番号などは画面にもサーバーにも一切出てこない。
 */
import { useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui";
import { api, ApiError, errorMessage } from "@/lib/api";
import { fmtTime, yen } from "@/lib/format";
import type { TerminalPayment } from "@/lib/types";

const POLL_MS = 2000;
const WAITING = new Set(["pending", "approved"]);

export function TerminalWaitDialog({
  initial,
  onCompleted,
  onClose,
}: {
  initial: TerminalPayment;
  onCompleted: (tp: TerminalPayment) => void;
  onClose: () => void;
}) {
  const [tp, setTp] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [note, setNote] = useState("");
  const done = useRef(false);

  // 結果が出るまで数秒おきに確認する（決済会社からの通知が届かなくても結果が取れる）
  useEffect(() => {
    if (!WAITING.has(tp.status)) return;
    let stop = false;
    const timer = setTimeout(async () => {
      try {
        const next = await api<TerminalPayment>(`/api/terminal-payments/${tp.id}`);
        if (!stop) {
          setOffline(false);
          setTp(next);
        }
      } catch (e) {
        if (!stop) {
          if (e instanceof ApiError && e.code === "network") setOffline(true);
          else setError(errorMessage(e));
          setTp((cur) => ({ ...cur })); // もう一度確認する
        }
      }
    }, POLL_MS);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [tp]);

  useEffect(() => {
    if (tp.status === "completed" && !done.current) {
      done.current = true;
      onCompleted(tp);
    }
  }, [tp, onCompleted]);

  async function act(path: string, body: unknown = {}) {
    setBusy(true);
    setError(null);
    try {
      setTp(await api<TerminalPayment>(`/api/terminal-payments/${tp.id}/${path}`, { method: "POST", body }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const waiting = WAITING.has(tp.status);
  const failed = tp.status === "declined" || tp.status === "canceled";
  const attention = tp.status === "needs_attention" && !tp.resolved_at;
  const cash = Number(tp.cash_amount) > 0;

  return (
    <Modal title={waiting ? "決済端末でお支払い中" : failed ? "カード決済できませんでした" : attention ? "要確認" : "決済端末"} onClose={waiting ? undefined : onClose}>
      <div className="space-y-4" data-testid="terminal-dialog" data-status={tp.status}>
        <div className="text-center space-y-1">
          <div className="text-lg" style={{ color: "var(--color-muted)" }}>
            {tp.terminal_name} ・ カード
          </div>
          <div className="font-bold num" style={{ fontSize: "4rem", lineHeight: 1.05 }}>
            {yen(tp.amount)}
          </div>
          {cash && (
            <div className="text-lg num" style={{ color: "var(--color-muted)" }}>
              ＋ 現金 {yen(tp.cash_amount)}（お預かり {yen(tp.cash_tendered)}・お釣り {yen(tp.change_amount)}）
            </div>
          )}
        </div>

        {tp.status === "pending" && (
          <div className="card-2 text-center space-y-1">
            <div className="text-2xl font-bold">端末にカードをかざす・差し込んでもらってください</div>
            <div className="text-base" style={{ color: "var(--color-muted)" }}>
              端末で決済が終わると、自動で会計まで完了します（{fmtTime(tp.created_at)} に送信）
            </div>
          </div>
        )}
        {tp.status === "approved" && <div className="text-2xl font-bold text-center">承認されました。会計を記録しています…</div>}
        {failed && (
          <p className="text-xl font-bold text-center" role="alert" style={{ color: "var(--color-danger)" }}>
            {tp.error_message || tp.status_label}
          </p>
        )}
        {attention && (
          <div className="space-y-2">
            <p className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {tp.error_message}
            </p>
            <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="どう対応したか（例: 手入力で会計した／端末で返金した）" />
            <button className="btn btn-xl w-full" disabled={busy || !note.trim()} onClick={() => act("resolve", { note })}>
              対応済みにする
            </button>
          </div>
        )}
        {offline && (
          <p className="text-base font-bold" style={{ color: "var(--color-warn)" }}>
            通信できません。つながったら自動で結果を確認します（端末の画面も確認してください）。
          </p>
        )}
        {error && (
          <p className="text-base font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}

        {tp.status === "pending" && tp.is_mock && (
          <div className="space-y-2 rounded-xl p-3" style={{ border: "2px dashed var(--color-warn)" }}>
            <div className="text-sm font-bold" style={{ color: "var(--color-warn)" }}>
              模擬端末（テスト用）: 実際の決済はしません。結果を選んでください。
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button className="btn btn-xl btn-ok" disabled={busy} onClick={() => act("simulate", { result: "approve" })}>
                （テスト）承認
              </button>
              <button className="btn btn-xl btn-danger" disabled={busy} onClick={() => act("simulate", { result: "decline" })}>
                （テスト）拒否
              </button>
            </div>
          </div>
        )}

        {tp.status === "pending" && (
          <button className="btn btn-lg w-full" disabled={busy} onClick={() => act("cancel")}>
            送信を取り消す（別の方法で払う）
          </button>
        )}
        {!waiting && (
          <button className="btn btn-xl w-full" onClick={onClose}>
            {failed ? "閉じる（もう一度送る・別の方法で払う）" : "閉じる"}
          </button>
        )}
      </div>
    </Modal>
  );
}

/** 端末では決済済みなのに会計にできなかったもの（会計画面の上に出す）。 */
export function TerminalAttentionList({ items, onResolved }: { items: TerminalPayment[]; onResolved: () => void }) {
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (items.length === 0) return null;
  return (
    <section className="card space-y-2" style={{ borderColor: "var(--color-danger)", borderWidth: 2 }} role="alert">
      <h2 className="text-xl font-bold" style={{ color: "var(--color-danger)" }}>
        要確認: 決済端末では決済済みの会計があります
      </h2>
      {items.map((t) => (
        <div key={t.id} className="card-2 space-y-2">
          <div className="text-lg">
            {t.table_name} 伝票#{t.slip_number} ・ カード <strong className="num">{yen(t.amount)}</strong>
            {t.provider_payment_id && <span className="text-sm"> （決済ID {t.provider_payment_id}）</span>}
          </div>
          <div className="text-base" style={{ color: "var(--color-muted)" }}>
            {t.error_message}
          </div>
          <div className="flex gap-2">
            <input
              className="input flex-1"
              value={notes[t.id] ?? ""}
              onChange={(e) => setNotes({ ...notes, [t.id]: e.target.value })}
              placeholder="どう対応したか（例: 手入力で会計した）"
            />
            <button
              className="btn btn-lg"
              disabled={busy === t.id || !(notes[t.id] ?? "").trim()}
              onClick={async () => {
                setBusy(t.id);
                setError(null);
                try {
                  await api(`/api/terminal-payments/${t.id}/resolve`, { method: "POST", body: { note: notes[t.id] } });
                  onResolved();
                } catch (e) {
                  setError(errorMessage(e));
                } finally {
                  setBusy(null);
                }
              }}
            >
              対応済み
            </button>
          </div>
        </div>
      ))}
      {error && (
        <p className="text-base font-bold" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </section>
  );
}

const TERMINAL_KEY = "noxia.terminalId";

export function rememberedTerminal(ids: number[]): number | null {
  try {
    const v = Number(window.localStorage.getItem(TERMINAL_KEY));
    if (ids.includes(v)) return v;
  } catch {
    /* 端末に保存できない環境（プライベートモード等）でも動く */
  }
  return ids[0] ?? null;
}

export function rememberTerminal(id: number) {
  try {
    window.localStorage.setItem(TERMINAL_KEY, String(id));
  } catch {
    /* noop */
  }
}
