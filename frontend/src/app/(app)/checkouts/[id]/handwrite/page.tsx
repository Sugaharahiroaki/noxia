"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { ErrorBox, Field, PageHeader, Spinner } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtTime, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Handwrite } from "@/lib/types";
import { activeIssues, type ReceiptInfo, type ReceiptIssue } from "@/lib/types_receipt_issue";
import { useLoad } from "@/lib/useLoad";

type HandwriteData = Handwrite & ReceiptInfo;

/** 手書き領収書への転記用。会計で確定した金額をそのまま大きく表示する（再計算しない）。渡したら発行を記録する。 */
export default function HandwritePage() {
  const { id } = useParams<{ id: string }>();
  const q = useLoad<HandwriteData>(`/api/checkouts/${id}/handwrite`);
  if (q.loading) return <Spinner />;
  if (q.error || !q.data) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const d = q.data;
  const hasReceivable = Number(d.receivable_amount) > 0;
  const issued = activeIssues(d);
  const lastIssued = issued[issued.length - 1];
  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <PageHeader
        title="手書き領収書への転記用"
        subtitle={
          <>
            会計#{d.checkout_number} ・ 伝票#{d.slip_number} ・ {d.table_name} ・ 営業日 {fmtDate(d.business_date)} ・ 確定 {fmtDateTime(d.confirmed_at)}
            {d.status === "voided" && <span className="ml-2 badge badge-danger">この会計は取消済みです</span>}
            {issued.length > 0 && <span className="ml-2 badge badge-ok">領収書 発行済（{issued.length}枚）</span>}
          </>
        }
        actions={
          <>
            <Link href="/floor" className="btn">
              卓一覧へ
            </Link>
            <Link href="/sales" className="btn">
              会計履歴へ
            </Link>
          </>
        }
      />
      {lastIssued && (
        <div className="card-2" style={{ borderColor: "var(--color-warn)" }} data-testid="receipt-issued-banner">
          <p className="font-semibold">この会計の領収書はお渡し済みです（{issued.length}枚）。</p>
          <p className="text-sm num">
            最後に渡した記録: {fmtDateTime(lastIssued.issued_at)} ・ {lastIssued.method_label} ・ {yen(lastIssued.amount)}
            {lastIssued.addressee && ` ・ 宛名 ${lastIssued.addressee}`}
          </p>
          <p className="text-sm">もう一度渡すときは、領収書に「再発行」と書いてください。</p>
        </div>
      )}
      <div className={`card-2 ${d.kind === "partial" ? "" : ""}`} style={d.kind === "partial" ? { borderColor: "var(--color-warn)" } : undefined}>
        <p className="font-semibold">{d.scope_note}</p>
        {d.kind === "partial" && (
          <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            伝票全体の会計合計（確定済み分）: {yen(d.visit_total_all_checkouts)}
            {d.other_checkouts.length > 0 && <> ・ 他の会計: {d.other_checkouts.map((c) => `#${c.number} ${yen(c.total)}`).join("、")}</>}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Big label="税込合計（領収金額）" value={yen(d.total)} primary />
        <Big label="税抜金額" value={yen(d.total_excl_tax)} />
        {d.tax_breakdown.map((t) => (
          <Big key={t.rate} label={`消費税（${Number(t.rate)}% 対象 ${yen(t.taxable_excl)}）`} value={yen(t.tax)} />
        ))}
        {Number(d.service_charge) > 0 && <Big label="うちサービス料（税込）" value={yen(d.service_charge)} small />}
        {Number(d.discount_total) > 0 && <Big label="値引き（反映済み）" value={"−" + yen(d.discount_total)} small />}
        {Number(d.rounding_adjustment) !== 0 && <Big label="端数調整（反映済み）" value={yen(d.rounding_adjustment, { sign: true })} small />}
      </div>

      <h2 className="font-bold text-lg mt-2">お支払い（領収書に書く金額）</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Big label="現金支払額" value={yen(d.cash_paid)} />
        <Big label="カード支払額" value={yen(d.card_paid)} />
        {hasReceivable && <Big label="未収（ツケ）額 ※領収額に含めない" value={yen(d.receivable_amount)} warn />}
      </div>
      {Number(d.cash_tendered) > 0 && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          参考（領収書には書かない）: 現金の預り額 {yen(d.cash_tendered)} ・ 釣銭 {yen(d.cash_change)}
        </p>
      )}

      <IssueRecorder d={d} issued={issued} onRecorded={() => q.reload()} />

      <details className="card-2">
        <summary className="cursor-pointer">この会計の明細（{d.lines.length}件）</summary>
        <ul className="mt-2 space-y-1 num">
          {d.lines.map((l, i) => (
            <li key={i} className="flex justify-between">
              <span>{l.name}</span>
              <span>{yen(l.amount)}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/**
 * 領収書を渡したら押す。記録すると「売上・会計履歴」に発行済みと出る。
 * 二度押し・再送で 2 件にならないよう、送信キーは記録できるまで同じものを使う。
 */
function IssueRecorder({ d, issued, onRecorded }: { d: HandwriteData; issued: ReceiptIssue[]; onRecorded: () => Promise<void> }) {
  const { can } = useSession();
  const [addressee, setAddressee] = useState("");
  const [proviso, setProviso] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [recorded, setRecorded] = useState<ReceiptIssue | null>(null);
  const sending = useRef(false);
  const idemKey = useRef(newIdempotencyKey());

  if (d.status !== "confirmed") return null;
  if (toInt(d.receipt_amount) <= 0) {
    return (
      <p className="card-2 text-sm" data-testid="receipt-not-needed">
        全額が後日のお支払い（ツケ）なので、領収書は出しません。回収したときにお渡しします。
      </p>
    );
  }
  const reissue = issued.length > 0;

  async function record() {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setErr(null);
    try {
      const r = await api<ReceiptIssue>(`/api/checkouts/${d.checkout_id}/receipt-issues`, {
        method: "POST",
        idempotencyKey: idemKey.current,
        body: { addressee, proviso },
      });
      idemKey.current = newIdempotencyKey();
      setRecorded(r);
      setAddressee("");
      setProviso("");
      await onRecorded();
    } catch (e) {
      setErr(e);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-3" data-testid="receipt-recorder">
      <div>
        <h2 className="font-bold text-lg">{reissue ? "もう一度渡したら記録する（再発行）" : "領収書を渡したら記録する"}</h2>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          記録すると「売上・会計履歴」に発行済みと出ます。あとで「もらっていない」と言われたときの確認に使います。宛名・但し書きは書いたときだけ入れてください。
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="宛名（書いた場合）">
          <input className="input" value={addressee} maxLength={60} onChange={(e) => setAddressee(e.target.value)} placeholder="例: 株式会社〇〇" data-testid="receipt-addressee" />
        </Field>
        <Field label="但し書き（書いた場合）">
          <input className="input" value={proviso} maxLength={40} onChange={(e) => setProviso(e.target.value)} placeholder="例: お飲食代として" data-testid="receipt-proviso" />
        </Field>
      </div>
      <button className="btn btn-xl btn-primary w-full" onClick={record} disabled={busy || !can("floor.operate")} data-testid="receipt-record">
        {busy ? "記録しています…" : reissue ? `再発行を記録する（${yen(d.receipt_amount)}）` : `領収書を渡した（${yen(d.receipt_amount)}）`}
      </button>
      {!can("floor.operate") && <p className="text-sm">記録する権限がありません。</p>}
      {recorded && (
        <p role="status" className="font-semibold" style={{ color: "var(--color-ok)" }} data-testid="receipt-recorded">
          記録しました（{fmtTime(recorded.issued_at)}
          {recorded.reissue ? "・再発行" : ""}）
        </p>
      )}
      <ErrorBox error={err} />
    </section>
  );
}

function Big({ label, value, primary, small, warn }: { label: string; value: string; primary?: boolean; small?: boolean; warn?: boolean }) {
  return (
    <div className="card" style={primary ? { borderColor: "var(--color-accent)" } : warn ? { borderColor: "var(--color-warn)" } : undefined}>
      <div className="text-sm" style={{ color: "var(--color-muted)" }}>
        {label}
      </div>
      <div className={`font-bold num ${primary ? "text-5xl" : small ? "text-2xl" : "text-4xl"}`} style={warn ? { color: "var(--color-warn)" } : undefined}>
        {value}
      </div>
    </div>
  );
}
