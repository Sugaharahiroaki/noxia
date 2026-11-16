"use client";

import { useState } from "react";
import { errorMessage } from "@/lib/api";
import { toInt, yen } from "@/lib/format";
import { Modal, NumberPad } from "./ui";

export const CANCEL_REASONS = ["入力ミス", "お客様のキャンセル", "提供できなかった"];
const DISCOUNT_REASONS = ["常連割引", "サービス", "端数調整"];

/** 理由を大きなボタンで選ぶ（その他は入力） */
export function ReasonModal({
  title,
  note,
  reasons,
  confirmLabel,
  danger,
  onClose,
  onSubmit,
}: {
  title: string;
  note?: string;
  reasons: string[];
  confirmLabel: string;
  danger?: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reason = picked === "その他" ? other.trim() : picked;
  return (
    <Modal
      title={title}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className={`btn btn-xl ${danger ? "btn-danger" : "btn-primary"}`}
            disabled={!reason || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(reason!);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "処理中…" : confirmLabel}
          </button>
        </>
      }
    >
      {note && <p className="text-lg mb-3">{note}</p>}
      <div className="text-base mb-2" style={{ color: "var(--color-muted)" }}>
        理由（記録されます）
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {[...reasons, "その他"].map((r) => (
          <button key={r} className={`btn btn-lg ${picked === r ? "btn-primary" : ""}`} onClick={() => setPicked(r)} aria-pressed={picked === r}>
            {r}
          </button>
        ))}
      </div>
      {picked === "その他" && <input className="input mt-3" autoFocus value={other} onChange={(e) => setOther(e.target.value)} placeholder="理由を入力" />}
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}

/** 値引き（料金調整）。金額と理由。値増しは扱わない（UNIFIED_PLAN 6 章 Q9） */
export function DiscountModal({ title = "値引き", onClose, onSubmit }: { title?: string; onClose: () => void; onSubmit: (amount: number, reason: string) => Promise<void> }) {
  const [amount, setAmount] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reason = picked === "その他" ? other.trim() : picked;
  return (
    <Modal
      title={title}
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button
            className="btn btn-xl btn-primary"
            disabled={!toInt(amount) || !reason || busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onSubmit(toInt(amount), reason!);
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "処理中…" : `${yen(toInt(amount))} 値引きする`}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-5xl font-bold num text-right mb-3">{yen(toInt(amount))}</div>
          <NumberPad value={amount} onChange={setAmount} quick={[500, 1000, 2000, 5000]} />
        </div>
        <div>
          <div className="text-base mb-2" style={{ color: "var(--color-muted)" }}>
            理由（記録されます）
          </div>
          <div className="grid gap-2">
            {[...DISCOUNT_REASONS, "その他"].map((r) => (
              <button key={r} className={`btn btn-lg ${picked === r ? "btn-primary" : ""}`} onClick={() => setPicked(r)}>
                {r}
              </button>
            ))}
          </div>
          {picked === "その他" && <input className="input mt-3" value={other} onChange={(e) => setOther(e.target.value)} placeholder="理由を入力" />}
          {error && (
            <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
              {error}
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}
